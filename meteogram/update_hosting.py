"""Aktualizuje data předpovědi a stránky webu na hostingu. Přístupy čte z prostředí GitHub Actions.

Plánovaný běh (každou hodinu) nahraje nový běh modelu a s ním přegenerované stránky stanic.
Push nebo ruční spuštění navíc nasadí statické soubory z web/ a stránky vygeneruje vždy.
"""

import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import stat

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
DATA = WEB / 'data'
SITE = ROOT / 'build' / 'site'
# z web/ se nenahrávají data (řeší publish), počty návštěv ani šablona index.html (nahrazuje ji vygenerovaná)
SKIP = ('data/', 'api/stats/')
# otisky nahraných souborů na hostingu; nahrává se jen to, co se od minula změnilo
MANIFEST = '.deploy-manifest.json'
RUN = re.compile(r'20\d{8}')


def read_runs(raw):
    runs = json.loads(raw)['runs']
    if not isinstance(runs, list) or any(not isinstance(run, str) or not RUN.fullmatch(run) for run in runs):
        raise ValueError('Neplatný seznam běhů modelu.')
    return runs


def remote_runs(sftp):
    """Seznam publikovaných běhů z hostingu (surový obsah index.json a běhy)."""
    with sftp.open('index.json', 'rb') as source:
        raw = source.read()
    return raw, read_runs(raw)


def restore(sftp):
    """Obnoví všechny publikované běhy. Potřeba jen před publikací nového běhu: fetch z nich skládá
    seznam běhů pro výběr na webu a nestahuje znovu stejné GRIB soubory."""
    DATA.mkdir(parents=True, exist_ok=True)
    raw, runs = remote_runs(sftp)
    for run in runs:
        dest = DATA / run
        dest.mkdir(exist_ok=True)
        for entry in sftp.listdir_attr(run):
            name = entry.filename
            if stat.S_ISREG(entry.st_mode) and re.fullmatch(r'[a-z0-9-]+\.json', name):
                sftp.get(f'{run}/{name}', str(dest / name))
        if not (dest / 'index.json').exists() or not (dest / 'summary.json').exists():
            raise RuntimeError(f'Neúplná publikovaná data běhu {run}.')
    (DATA / 'index.json').write_bytes(raw)
    return runs


def restore_latest(sftp, raw, runs):
    """Pro samotné nasazení webu stačí z posledního běhu seznam stanic a souhrn (čte je build_pages)."""
    if not runs:
        raise RuntimeError('Na hostingu není žádný publikovaný běh.')
    dest = DATA / runs[0]
    dest.mkdir(parents=True, exist_ok=True)
    for name in ('index.json', 'summary.json'):
        sftp.get(f'{runs[0]}/{name}', str(dest / name))
    (DATA / 'index.json').write_bytes(raw)


def publish(sftp, previous_runs):
    """Nejprve dokončí nové adresáře, potom atomicky přepne veřejný seznam běhů."""
    runs = read_runs((DATA / 'index.json').read_bytes())
    if not runs:
        raise RuntimeError('Chybí zpracovaná předpověď; publikace zrušena.')
    if runs == previous_runs:
        print('Novější běh zatím není k dispozici.', flush=True)
        return False
    for run in runs:
        if run in previous_runs:
            continue
        try:
            sftp.mkdir(run)
        except OSError:
            # Adresář mohl zůstat po přerušeném přenosu. Soubory znovu přepíšeme.
            if not stat.S_ISDIR(sftp.stat(run).st_mode):
                raise
        for source in sorted((DATA / run).glob('*.json')):
            sftp.put(str(source), f'{run}/{source.name}')
    sftp.put(str(DATA / 'index.json'), '.index.json.uploading')
    # OpenSSH posix-rename nahradí index jednou operací. Bez podpory bezpečně selže.
    sftp.posix_rename('.index.json.uploading', 'index.json')
    print(f'Publikován běh {runs[0]}.', flush=True)
    # Starší adresáře na hostingu nemažeme; veřejný index drží KEEP_RUNS běhů.
    return True


def upload(sftp, files):
    """Nahraje změněné soubory {cesta na hostingu: místní soubor}; každý se přepne až po úplném přenosu."""
    try:
        with sftp.open(MANIFEST, 'rb') as f:
            known = json.loads(f.read())
    except (OSError, ValueError):
        known = {}
    hashes = {remote: hashlib.sha256(Path(source).read_bytes()).hexdigest() for remote, source in files.items()}
    changed = {remote: source for remote, source in files.items() if known.get(remote) != hashes[remote]}
    dirs = set()
    for remote, source in sorted(changed.items()):
        parent = posixpath.dirname(remote)
        parts = parent.split('/') if parent else []
        for i in range(len(parts)):
            d = '/'.join(parts[:i + 1])
            if d in dirs:
                continue
            try:
                sftp.mkdir(d)
            except OSError:
                if not stat.S_ISDIR(sftp.stat(d).st_mode):
                    raise
            dirs.add(d)
        sftp.put(str(source), remote + '.uploading')
        sftp.posix_rename(remote + '.uploading', remote)
    with sftp.open(MANIFEST + '.uploading', 'wb') as f:
        f.write(json.dumps({**known, **hashes}, sort_keys=True).encode())
    sftp.posix_rename(MANIFEST + '.uploading', MANIFEST)
    print(f'Nahráno {len(changed)} změněných souborů z {len(files)}.', flush=True)
    for remote in sorted(changed):
        print(f'  {remote}', flush=True)


def site_files(static):
    files = {}
    if static:
        for f in WEB.rglob('*'):
            rel = f.relative_to(WEB).as_posix()
            if f.is_file() and rel != 'index.html' and f.name != '.DS_Store' and not rel.startswith(SKIP):
                files[rel] = f
    for f in SITE.rglob('*'):
        if f.is_file():
            files[f.relative_to(SITE).as_posix()] = f
    return files


def main():
    import paramiko
    import build_pages
    import fetch

    keys = ('SFTP_HOST', 'SFTP_PORT', 'SFTP_USER', 'SFTP_PASSWORD', 'SFTP_REMOTE_DIR')
    config = {key: os.environ.get(key, '') for key in keys}
    for key, value in config.items():
        if not value:
            raise ValueError(f'Chybí GitHub Secret {key}.')
    remote = config['SFTP_REMOTE_DIR'].rstrip('/')
    if remote != '/public_html':
        raise ValueError('Očekávaný cílový adresář je /public_html.')
    with paramiko.SSHClient() as client:
        client.load_host_keys(str(ROOT / 'meteogram' / 'hosting_known_hosts'))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            config['SFTP_HOST'], port=int(config['SFTP_PORT']),
            username=config['SFTP_USER'], password=config['SFTP_PASSWORD'],
            look_for_keys=False, allow_agent=False, timeout=30,
            auth_timeout=30, banner_timeout=30,
        )
        client.get_transport().set_keepalive(30)
        with client.open_sftp() as sftp:
            sftp.chdir(posixpath.join(remote, 'data'))
            raw, previous = remote_runs(sftp)
        runs = fetch.list_runs()
        needed = set(fetch.FIELDS) | {fetch.OROGRAPHY}
        complete = sorted((run for run, fields in runs.items() if needed <= fields.keys()), reverse=True)
        if not complete:
            raise RuntimeError('ČHMÚ neposkytlo žádný úplný běh.')
        published = False
        deploy = os.environ.get('GITHUB_EVENT_NAME') != 'schedule'
        if previous and complete[0] <= max(previous):
            print('Na hostingu už je nejnovější dostupný běh.', flush=True)
            if deploy:
                with client.open_sftp() as sftp:
                    sftp.chdir(posixpath.join(remote, 'data'))
                    restore_latest(sftp, raw, previous)
        else:
            with client.open_sftp() as sftp:
                sftp.chdir(posixpath.join(remote, 'data'))
                previous = restore(sftp)
            if not fetch.main():
                raise RuntimeError('Nový běh se nepodařilo zpracovat; hosting zůstává beze změny.')
            with client.open_sftp() as sftp:
                sftp.chdir(posixpath.join(remote, 'data'))
                published = publish(sftp, previous)
        if published or deploy:
            build_pages.main(SITE, DATA)
            with client.open_sftp() as sftp:
                sftp.chdir(remote)
                upload(sftp, site_files(static=deploy))


if __name__ == '__main__':
    main()
