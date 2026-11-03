"""Aktualizuje jen veřejná JSON data. Přístupy čte z prostředí GitHub Actions."""

import json
import os
from pathlib import Path
import posixpath
import re
import stat

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'web' / 'data'
RUN = re.compile(r'20\d{8}')


def read_runs(raw):
    runs = json.loads(raw)['runs']
    if not isinstance(runs, list) or any(not isinstance(run, str) or not RUN.fullmatch(run) for run in runs):
        raise ValueError('Neplatný seznam běhů modelu.')
    return runs


def restore(sftp):
    """Obnoví publikované běhy, aby se nestahovaly stejné GRIB soubory každou hodinu."""
    DATA.mkdir(parents=True, exist_ok=True)
    with sftp.open('index.json', 'rb') as source:
        raw = source.read()
    runs = read_runs(raw)
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


def publish(sftp, previous_runs):
    """Nejprve dokončí nové adresáře, potom atomicky přepne veřejný seznam běhů."""
    runs = read_runs((DATA / 'index.json').read_bytes())
    if not runs:
        raise RuntimeError('Chybí zpracovaná předpověď; publikace zrušena.')
    if runs == previous_runs:
        print('Novější běh zatím není k dispozici.', flush=True)
        return
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


def main():
    import paramiko
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
            previous = restore(sftp)
        runs = fetch.list_runs()
        needed = set(fetch.FIELDS) | {fetch.OROGRAPHY}
        complete = sorted((run for run, fields in runs.items() if needed <= fields.keys()), reverse=True)
        if not complete:
            raise RuntimeError('ČHMÚ neposkytlo žádný úplný běh.')
        if previous and complete[0] <= max(previous):
            print('Na hostingu už je nejnovější dostupný běh.', flush=True)
            return
        if not fetch.main():
            raise RuntimeError('Nový běh se nepodařilo zpracovat; hosting zůstává beze změny.')
        with client.open_sftp() as sftp:
            sftp.chdir(posixpath.join(remote, 'data'))
            publish(sftp, previous)


if __name__ == '__main__':
    main()
