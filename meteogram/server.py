"""Lokální server: servíruje web/ a každých 20 minut zkontroluje nový běh ALADINu."""

import os
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import fetch

PORT = int(os.environ.get("PORT", 8765))
CHECK_EVERY_S = 20 * 60


# stránky, které vykresluje přímo aplikace (na Webglobe to samé řeší web/.htaccess)
APP_ROUTES = {"/o-projektu", "/o-projektu/", "/ochrana-soukromi", "/ochrana-soukromi/"}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0].split("#")[0] in APP_ROUTES:
            self.path = "/index.html"
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def updater():
    while True:
        try:
            fetch.main()
        except Exception as e:
            fetch.log(f"aktualizace selhala: {e}")
        time.sleep(CHECK_EVERY_S)


if __name__ == "__main__":
    threading.Thread(target=updater, daemon=True).start()
    web = fetch.ROOT / "web"
    server = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Handler, directory=str(web)))
    fetch.log(f"meteogram běží na http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
