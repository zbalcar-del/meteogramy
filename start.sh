#!/bin/sh
# Spustí lokální meteogram na http://localhost:8765 (data se aktualizují samy)
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install -q eccodes numpy
fi
exec .venv/bin/python meteogram/server.py
