"""Lokální server: servíruje web/ a každých 20 minut zkontroluje nový běh ALADINu.

Stránky stanic a pohoří (/praded/, /krkonose/) bere z build/site, které vytváří build_pages.py.
"""

import os
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import build_pages
import fetch

PORT = int(os.environ.get("PORT", 8765))
CHECK_EVERY_S = 20 * 60
SITE = fetch.ROOT / "build" / "site"


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        # vygenerované stránky mají přednost; index.html se ale bere vždy čerstvý z web/,
        # aby se úpravy aplikace projevily bez nového generování
        local = super().translate_path(path)
        rel = Path(local).relative_to(self.directory)
        built = SITE / rel
        if rel.parts and (built.is_file() or (built / "index.html").is_file()):
            return str(built)
        return local

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def build():
    try:
        build_pages.main(SITE)
    except Exception as e:
        fetch.log(f"generování stránek selhalo: {e}")


def updater():
    while True:
        try:
            fetch.main()
        except Exception as e:
            fetch.log(f"aktualizace selhala: {e}")
        build()
        time.sleep(CHECK_EVERY_S)


if __name__ == "__main__":
    build()
    threading.Thread(target=updater, daemon=True).start()
    web = fetch.ROOT / "web"
    server = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Handler, directory=str(web)))
    fetch.log(f"meteogram běží na http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
