"""Zimy na horách: denní výška sněhu ČHMÚ pro Krkonoše a Hrubý Jeseník (python3 meteogram/winters.py).

Historická denní data ČHMÚ (climate/historical, končí vždy předchozím rokem) se stáhnou jednou
do build/historie a doplní se měsíčními soubory climate/recent. Výsledek je jeden kompaktní JSON
na pohoří ve web/data/historie/<pohoří>.json: pro každou zimu pole denních výšek od 1. 10. do 31. 5.
(null = chybějící nebo nepoužitelné měření). Souhrny sezón počítá až stránka.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import urllib.error
import urllib.request

from snow import numeric, table
from build_pages import slug

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'web' / 'data' / 'historie'
CACHE = ROOT / 'build' / 'historie'
HISTORICAL = 'https://opendata.chmi.cz/meteorology/climate/historical/data/daily/dly-{}.json'
RECENT = 'https://opendata.chmi.cz/meteorology/climate/recent/data/daily/'
RANGES = ('Krkonoše', 'Hrubý Jeseník')
MIN_SEASONS = 3            # stanice s kratší řadou na stránce nedávají smysl
MIN_DAYS = 120             # zima s méně měřeními se nebere jako úplná
UA = {'User-Agent': 'Meteogramy.cz/1.0 (+https://meteogramy.cz/o-projektu/)'}


def get(url, limit=200 * 1024 * 1024):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Odpověď ČHMÚ je příliš velká.')
    return raw


def season_of(day):
    """Zima 2025/26 = 1. 10. 2025 až 31. 5. 2026; letní dny do žádné zimy nepatří."""
    if 6 <= day.month <= 9:
        return None
    start = day.year if day.month >= 10 else day.year - 1
    return start


def depths(rows):
    out = {}
    for row in rows:
        if row.get('ELEMENT') != 'SCE' or row.get('VTYPE') != '06:00':
            continue
        value = numeric(row, depth=True)
        if value is not None:
            out[date.fromisoformat(row['DT'][:10])] = value
    return out


def historical(wsi):
    """Historická řada se mění jen jednou ročně, proto se drží zpracovaná v cache."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f'{wsi}.json'
    if path.exists() and datetime.now().timestamp() - path.stat().st_mtime < 30 * 86400:
        return {date.fromisoformat(k): v for k, v in json.loads(path.read_text()).items()}
    try:
        values = depths(table(get(HISTORICAL.format(wsi))))
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        values = {}
    path.write_text(json.dumps({d.isoformat(): v for d, v in sorted(values.items())}))
    return values


def recent(wsi, today):
    """Měsíce od začátku roku: aktuální je v kořeni, starší ve složkách 01/ … 12/."""
    values = {}
    for month in range(1, today.month + 1):
        period = f'{today.year}{month:02d}'
        folder = '' if month == today.month else f'{month:02d}/'
        try:
            values.update(depths(table(get(f'{RECENT}{folder}dly-{wsi}-{period}.json', 8 * 1024 * 1024))))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    return values


def seasons(values, today):
    by = {}
    for day, value in values.items():
        start = season_of(day)
        if start is not None:
            by.setdefault(start, {})[day] = value
    result = {}
    for start, days in sorted(by.items()):
        first = date(start, 10, 1)
        length = (date(start + 1, 6, 1) - first).days
        series = [days.get(first + timedelta(i)) for i in range(length)]
        while series and series[-1] is None:
            series.pop()
        current = first <= today < date(start + 1, 6, 1)
        if current or sum(v is not None for v in series) >= MIN_DAYS:
            result[f'{start}/{str(start + 1)[2:]}'] = [None if v is None else round(v) for v in series]
    return result


def build(today=None):
    today = today or datetime.now(timezone.utc).date()
    snow = json.loads((ROOT / 'web' / 'data' / 'snow.json').read_text())
    stations = [s for s in snow['stations'] if s['range'] in RANGES and s.get('source_id')]

    def load(station):
        values = historical(station['source_id'])
        values.update(recent(station['source_id'], today))
        return station, seasons(values, today)

    with ThreadPoolExecutor(max_workers=4) as pool:
        loaded = list(pool.map(load, stations))
    OUT.mkdir(parents=True, exist_ok=True)
    for name in RANGES:
        items = [{'id': s['id'], 'name': s['name'], 'alt': s['alt'], 'seasons': w}
                 for s, w in loaded if s['range'] == name and len(w) >= MIN_SEASONS]
        items.sort(key=lambda s: -s['alt'])
        doc = {'version': 1, 'range': name, 'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
               'source': 'ČHMÚ – denní výška sněhu v 06:00 UTC', 'stations': items}
        (OUT / f'{slug(name)}.json').write_text(json.dumps(doc, ensure_ascii=False, separators=(',', ':')))
        print(f'{name}: {len(items)} stanic, {sum(len(s["seasons"]) for s in items)} zim.', flush=True)


if __name__ == '__main__':
    build()
