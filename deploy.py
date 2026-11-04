"""Ruční nasazení statických souborů z web/ přes SFTP: python3 deploy.py [--dry-run].

Běžně se web nasazuje pushem do main (GitHub akce update-data.yml), která nahraje i vygenerované
stránky s aktuální předpovědí. Volba --pages obnoví metadata z produkce a nasadí také
HTML stránky, aniž by nahrazovala publikované běhy modelu místními daty.
"""

import argparse
import json
import re
import sys
import urllib.request
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import quote as urlquote, quote_plus


def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`') + '"'


def prepare_pages(root, reader=None):
    """Pro ruční build použije aktuální veřejná produkční data, nikoli lokální běh."""
    if reader is None:
        def reader(path):
            request = urllib.request.Request('https://meteogramy.cz/data/' + path,
                                             headers={'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError('Příliš velká odpověď produkčního webu.')
            return raw
    data = root / 'build/manual-deploy/data'
    index = reader('index.json')
    runs = json.loads(index)['runs']
    if not isinstance(runs, list) or not runs or any(not isinstance(run, str) or not re.fullmatch(r'20\d{8}', run) for run in runs):
        raise ValueError('Neplatný produkční seznam běhů.')
    # Vše nejdříve načteme a ověříme. Neúplný podklad se na hosting neposílá.
    payloads = {'index.json': index}
    for path in [f'{runs[0]}/index.json', f'{runs[0]}/summary.json', 'snow.json', 'avalanche.json']:
        raw = reader(path)
        json.loads(raw)
        payloads[path] = raw
    for path, raw in payloads.items():
        destination = data / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    sys.path.insert(0, str(root / 'meteogram'))
    import build_pages
    return build_pages.main(root / 'build/manual-deploy/site', data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Pouze vypíše plánované přenosy.')
    parser.add_argument('--pages', action='store_true', help='Nasadí také HTML stránky sestavené z aktuálních produkčních dat.')
    parser.add_argument('--list', action='store_true', help='Zobrazí cílovou složku bez zápisu.')
    parser.add_argument('--remove-wordpress', action='store_true', help='Po přenosu odstraní soubory původního WordPressu bez zálohy.')
    args = parser.parse_args()
    if args.pages and args.list:
        parser.error('--pages nelze kombinovat s --list.')
    if args.remove_wordpress and (args.dry_run or args.list):
        parser.error('--remove-wordpress nelze kombinovat s kontrolními režimy.')
    root = Path(__file__).resolve().parent
    env_path = root / '.env'
    if not env_path.exists():
        parser.error('Chybí soubor .env.')
    os.chmod(env_path, 0o600)
    config = {}
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, separator, value = line.partition('=')
            if separator:
                config[key.strip()] = value
    for key in ('SFTP_HOST', 'SFTP_PORT', 'SFTP_USER', 'SFTP_PASSWORD', 'SFTP_REMOTE_DIR'):
        if not config.get(key):
            parser.error(f'Doplň {key} v .env.')
    if args.pages and config['SFTP_REMOTE_DIR'].rstrip('/') != '/public_html':
        parser.error('Nasazení stránek očekává cílovou složku /public_html.')
    if not config['SFTP_PORT'].isdigit():
        parser.error('SFTP_PORT musí být číslo.')
    if not shutil.which('lftp'):
        parser.error('Chybí lftp (instalace: brew install lftp).')
    pages = prepare_pages(root) if args.pages else None
    # Heslo předáváme přes standardní vstup, nikdy jako argument procesu.
    # Záměrně bez --delete: soubory na hostingu nemažeme.
    known_hosts = root / '.env.known_hosts'
    ssh = 'ssh -a -x'
    if known_hosts.exists():
        ssh += ' -oStrictHostKeyChecking=yes -oUserKnownHostsFile=.env.known_hosts'
    commands = '\n'.join([
        'set cmd:fail-exit yes',
        f'set sftp:connect-program {quote(ssh)}',
        'set net:max-retries 1',
        'set net:timeout 20',
        f'open -p {config["SFTP_PORT"]} -u {quote(config["SFTP_USER"] + "," + config["SFTP_PASSWORD"])} {quote("sftp://" + config["SFTP_HOST"])}',
        *([f'cd {quote(config["SFTP_REMOTE_DIR"])}', 'cls -la'] if args.list else [
            f'cd {quote(config["SFTP_REMOTE_DIR"])}',
            f'mirror --reverse --verbose {"--dry-run " if args.dry_run else ""}--no-perms --exclude-glob .DS_Store --exclude-glob data/ --exclude-glob api/stats/ --exclude-glob index.html {quote(str(root / "web"))} .',
            *([f'mirror --reverse --verbose {"--dry-run " if args.dry_run else ""}--no-perms {quote(str(pages))} .'] if pages else []),
            *([] if args.dry_run else ['rm -f .DS_Store data/.DS_Store']),
            *([
                'rm -r wp-admin wp-content wp-includes',
                'rm .htaccess.bk index.php license.txt readme.html wp-activate.php wp-blog-header.php wp-comments-post.php wp-config-sample.php wp-config.php wp-cron.php wp-links-opml.php wp-load.php wp-login.php wp-mail.php wp-settings.php wp-signup.php wp-trackback.php xmlrpc.php',
            ] if args.remove_wordpress else []),
        ]),
        'bye',
        '',
    ])
    result = subprocess.run(
        ['lftp', '--norc', '-f', '/dev/stdin'], input=commands, text=True,
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    # lftp --dry-run může vypsat úplné URL včetně hesla. Výstup vždy očistíme.
    output = result.stdout
    password = config['SFTP_PASSWORD']
    for secret in sorted({password, urlquote(password, safe=''), quote_plus(password)}, key=len, reverse=True):
        output = output.replace(secret, '***')
    print(output, end='')
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
