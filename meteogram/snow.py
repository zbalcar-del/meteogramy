"""Denní naměřená výška sněhu ČHMÚ (SCE), nikoli předpověď modelu.

Stahuje pouze existující horské stanice podle GH_ID/WSI. Chybějící a podezřelé
hodnoty zůstávají chybějící; změna se počítá jen mezi sousedními denními termíny.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import escape
import json
import math
from pathlib import Path
import re
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'web' / 'data' / 'snow.json'
BASE = 'https://opendata.chmi.cz/meteorology/climate/recent/'
SOURCE = 'https://www.chmi.cz/namerena-data/data-z-mericich-stanic/vyska-snehu'
TZ = ZoneInfo('Europe/Prague')
FLAGS = {'A': 'Ovlivněno umělým sněžením', 'N': 'Nesouvislá pokrývka',
         'P': 'Poprašek', 'U': 'Měření není možné', 'Z': 'Neplatná záporná hodnota'}


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_time(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Čas měření musí obsahovat časové pásmo.')
    return dt.astimezone(timezone.utc)


def request(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Meteogramy.cz/1.0 (+https://meteogramy.cz/o-projektu/)'})
    with urllib.request.urlopen(req, timeout=25) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Odpověď ČHMÚ je příliš velká.')
    return raw


def table(raw):
    data = json.loads(raw)['data']['data']
    columns = data['header'].split(',')
    return [dict(zip(columns, row)) for row in data['values']]


def numeric(row, depth=False):
    value = row.get('VAL')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if row.get('QUALITY') not in (0, 3, 5) or row.get('FLAG') in ('U', 'Z'):
        return None
    if depth and value < 0:
        return None
    return round(value, 1)


def station_record(place, wsi, rows, now):
    """Nejnovější hlášení se nezaměňuje se starším, pokud v něm údaj chybí."""
    result = {k: place[k] for k in ('id', 'name', 'range', 'alt', 'station')}
    result.update({'source_id': wsi, 'observed_at': None, 'depth': None, 'change': None,
                   'notes': [], 'history': []})
    by_time = {}
    for row in rows:
        if row.get('STATION') != wsi or row.get('ELEMENT') not in ('SCE', 'SCEdif') or row.get('VTYPE') != '06:00':
            continue
        dt = parse_time(row['DT'])
        if dt > now:
            continue
        fields = by_time.setdefault(dt, {})
        previous = fields.get(row['ELEMENT'])
        if previous is None or row.get('QUALITY') == 0:
            fields[row['ELEMENT']] = row
    dates = sorted(dt for dt, fields in by_time.items() if 'SCE' in fields)
    if not dates:
        return result
    latest = dates[-1]
    row = by_time[latest]['SCE']
    result['observed_at'] = iso(latest)
    result['depth'] = numeric(row, depth=True)
    flag = row.get('FLAG', '')
    if flag:
        result['notes'].append(FLAGS.get(flag, 'Doplněk k měření: ' + str(flag)))
    if row.get('QUALITY') == 3:
        result['notes'].append('Odhadnutá hodnota')
    elif row.get('QUALITY') == 5:
        result['notes'].append('Kvalita zatím neověřena')
    elif row.get('QUALITY') in (1, 2):
        result['notes'].append('Podezřelá hodnota nezobrazena')
    if result['depth'] is not None and flag not in ('P', 'N'):
        change_row = by_time[latest].get('SCEdif')
        if change_row and not change_row.get('FLAG'):
            result['change'] = numeric(change_row)
        if result['change'] is None:
            previous = by_time.get(latest - timedelta(days=1), {}).get('SCE')
            if previous and not previous.get('FLAG') and numeric(previous, depth=True) is not None:
                result['change'] = round(result['depth'] - numeric(previous, depth=True), 1)
    for dt in dates:
        value = numeric(by_time[dt]['SCE'], depth=True)
        if value is not None and dt >= latest - timedelta(days=6):
            result['history'].append({'at': iso(dt), 'depth': value})
    return result


def collect(now=None, reader=request):
    now = now or datetime.now(timezone.utc)
    listing = reader(BASE + 'metadata/').decode('utf-8')
    dates1 = set(re.findall(r'href="meta1-(\d{8})\.json"', listing))
    dates2 = set(re.findall(r'href="meta2-(\d{8})\.json"', listing))
    dates = sorted(dates1 & dates2, reverse=True)
    if not dates:
        raise ValueError('ČHMÚ neposkytlo metadata sněhových měření.')
    day = dates[0]
    metadata = table(reader(BASE + f'metadata/meta1-{day}.json'))
    elements = table(reader(BASE + f'metadata/meta2-{day}.json'))
    stations = {row['GH_ID']: row for row in metadata}
    available = {row['WSI'] for row in elements if row['OBS_TYPE'] == 'DLY'
                 and row['EG_EL_ABBREVIATION'] == 'SCE' and row['UN_DESCRIPTION'] == 'cm'
                 and row['SCHEDULE'] == '06:00'}
    places = json.loads((ROOT / 'meteogram' / 'places.json').read_text())
    month = now.strftime('%Y%m')
    months = [month]
    if now.day <= 8:
        months.append((now.replace(day=1) - timedelta(days=1)).strftime('%Y%m'))
    failures = []
    successes = []

    def load(place):
        meta = stations.get(place['station'])
        wsi = meta['WSI'] if meta else None
        rows = []
        if wsi in available:
            for period in months:
                folder = '' if period == month else period[-2:] + '/'
                url = BASE + f'data/daily/{folder}dly-{wsi}-{period}.json'
                try:
                    rows.extend(table(reader(url)))
                    successes.append(place['id'])
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        failures.append(place['id'])
                except (OSError, ValueError, KeyError, TypeError):
                    failures.append(place['id'])
        return station_record(place, wsi, rows, now)

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(load, places))
    measured = [row for row in records if row['depth'] is not None]
    target_ids = {p['station'] for p in places}
    targets = {r['WSI'] for r in metadata if r['GH_ID'] in target_ids} & available
    # Při výpadku nezahodíme poslední použitelný snapshot na hostingu ani lokálně.
    if len(set(successes)) < max(1, len(targets) // 2) or not measured:
        raise RuntimeError('Sněhová data nejsou úplná; poslední publikované měření zachováno.')
    return {'version': 1, 'retrieved_at': iso(now), 'source': SOURCE,
            'latest_at': max(row['observed_at'] for row in measured),
            'partial': bool(failures), 'stations': records}


def main():
    snapshot = collect()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    temporary.replace(OUT)
    print(f'Sníh: {sum(r["depth"] is not None for r in snapshot["stations"])} stanic s měřením.', flush=True)
    return snapshot


def fmt(value):
    return f'{value:g}'.replace('.', ',').replace('-', '−')


def stamp(value):
    dt = parse_time(value).astimezone(TZ)
    return f'{dt.day}. {dt.month}. {dt.hour:02d}:{dt.minute:02d} {"SELČ" if dt.dst() else "SEČ"}'


def sparkline(history):
    if len(history) < 2:
        return '<span class="snow-trend-empty">Málo měření</span>'
    start, end = parse_time(history[0]['at']), parse_time(history[-1]['at'])
    high = max(max(row['depth'] for row in history), 1)
    points = ' '.join(f'{4 + (parse_time(row["at"]) - start).total_seconds() / (end - start).total_seconds() * 88:.1f},{30 - row["depth"] / high * 24:.1f}' for row in history)
    label = 'Vývoj dostupných denních měření: ' + ', '.join(f'{stamp(r["at"])}: {fmt(r["depth"])} cm' for r in history)
    return f'<svg viewBox="0 0 96 36" role="img" aria-label="{escape(label, quote=True)}"><path d="M4 31H92" stroke="#e5ebf3"/><polyline points="{points}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'


def render(snapshot, now=None):
    """Jediný renderer pro statickou stránku i fragment načítaný při SPA navigaci."""
    now = now or datetime.now(timezone.utc)
    if not snapshot or not snapshot.get('stations'):
        return '<div class="snow-empty" role="status"><h2>Měření se právě připravují</h2><p>Sněhová data ČHMÚ zatím nejsou dostupná. Zkuste stránku později.</p></div>'
    records = snapshot['stations']
    measured = [row for row in records if row['depth'] is not None]
    if not measured:
        return '<div class="snow-empty" role="status"><h2>Měření nejsou dostupná</h2><p>Chybějící údaj neznamená nulovou sněhovou pokrývku.</p></div>'
    latest = parse_time(snapshot['latest_at'])
    # Maximum bere poslední dostupné měření každé stanice, stejně jako přehled níže.
    deepest = max(measured, key=lambda row: (row['depth'], row['observed_at']))
    # Denní přírůstky nadále porovnáváme ve stejném posledním denním termínu.
    comparable = [row for row in measured if row['observed_at'] == snapshot['latest_at']]
    rising = [row for row in comparable if row['change'] is not None and row['change'] > 0]
    gain = max(rising, key=lambda row: row['change']) if rising else None
    known_changes = any(row['change'] is not None for row in comparable)
    gain_value = '+' + fmt(gain['change']) if gain else '0' if known_changes else '—'
    gain_label = escape(gain['name']) if gain else 'Bez hlášeného přírůstku' if known_changes else 'Změna není k dispozici'
    stale = now - latest > timedelta(hours=36)
    status = 'Starší dostupné měření' if stale else 'Poslední denní měření'
    out = (f'<div class="snow-updated"><span class="snow-status{" old" if stale else ""}">{status}</span>'
           f'<time datetime="{escape(snapshot["latest_at"])}">{stamp(snapshot["latest_at"])}</time>'
           f'<span class="snow-collected">Data ověřena {stamp(snapshot["retrieved_at"])}</span></div>'
           '<div class="snow-highlights">'
           f'<a class="snow-highlight snow-highlight-main" href="/{deepest["id"]}/"><span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2v20M3.34 7l17.32 10M3.34 17L20.66 7M9 4l3 3 3-3M9 20l3-3 3 3M3.6 10.5l4.1-1.1-1.1-4.1M17.4 18.7l-1.1-4.1 4.1-1.1M3.6 13.5l4.1 1.1-1.1 4.1M17.4 5.3l-1.1 4.1 4.1 1.1"/></svg>Nejvyšší pokrývka</span><strong>{fmt(deepest["depth"])}<small> cm</small></strong>'
           f'<b>{escape(deepest["name"])}</b><small>{escape(deepest["range"])} · {deepest["alt"]} m</small><small>Měřeno <time datetime="{escape(deepest["observed_at"], quote=True)}">{stamp(deepest["observed_at"])}</time></small><span class="snow-card-foot">Otevřít meteogram →</span></a>'
           f'<div class="snow-highlight"><span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>Největší denní přírůstek</span><strong>{gain_value}<small>{" cm" if gain or known_changes else ""}</small></strong>'
           f'<b>{gain_label}</b><small>Ve stejném termínu měření</small><span class="snow-card-foot">Změna pokrývky za 24 hodin</span></div>'
           f'<div class="snow-highlight"><span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>Stanice s měřením</span><strong>{len(measured)}<small> / {len(records)}</small></strong>'
           f'<b>Horské stanice ČHMÚ</b><small>Čas měření uvádíme u každé stanice</small><span class="snow-card-foot">Dostupnost denních měření</span></div></div>')
    if snapshot.get('partial'):
        out += '<p class="snow-notice" role="status">Část dat se nepodařilo obnovit. U stanic zkontrolujte datum měření.</p>'
    out += ('<div class="snow-results-head"><h2>Přehled podle pohoří</h2><span id="snow-count" role="status" aria-live="polite"></span></div>'
            '<div id="snow-groups">')
    ranges = sorted({r['range'] for r in measured}, key=lambda name: -max(r['alt'] for r in measured if r['range'] == name))
    for name in ranges:
        group = [row for row in measured if row['range'] == name]
        group.sort(key=lambda row: (row['depth'] is None, -(row['depth'] or 0), -row['alt']))
        out += (f'<section class="snow-group" data-range="{escape(name, quote=True)}"><div class="snow-group-head"><h3>{escape(name)}</h3>'
                f'<span>{len(group)} {"stanice" if len(group) < 5 else "stanic"} s měřením</span></div>'
                '<div class="snow-table-head" aria-hidden="true"><span>Stanice</span><span>Výška sněhu</span><span>Denní změna</span><span>Vývoj</span><span>Měřeno</span></div><div class="snow-rows">')
        for row in group:
            depth = row['depth']
            notes = list(row.get('notes', []))
            if row['observed_at'] and now - parse_time(row['observed_at']) > timedelta(hours=36):
                notes.append('Starší měření')
            value = '—' if depth is None else 'Poprašek' if 'Poprašek' in notes else fmt(depth)
            change = row['change']
            change_text = '—' if change is None else ('+' if change > 0 else '') + fmt(change) + ' cm'
            measured_html = (f'<time datetime="{escape(row["observed_at"], quote=True)}">{stamp(row["observed_at"])}</time>'
                             if row['observed_at'] else 'Není k dispozici')
            out += (f'<a class="snow-row" href="/{row["id"]}/" data-name="{escape(row["name"], quote=True)}" data-alt="{row["alt"]}" '
                    f'data-depth="{"" if depth is None else depth}" data-change="{"" if change is None else change}" data-measured="{escape(row["observed_at"] or "", quote=True)}">'
                    f'<span class="snow-place"><b>{escape(row["name"])}</b><small>{row["alt"]} m n. m.</small>'
                    + (f'<em>{escape(" · ".join(notes))}</em>' if notes else '') + '</span>'
                    f'<span class="snow-depth">{value}<small>{" cm" if depth is not None and value != "Poprašek" else ""}</small>'
                    f'{"<em>Bez hlášení</em>" if depth is None else ""}</span>'
                    f'<span class="snow-change{" positive" if change is not None and change > 0 else ""}"><small>Denní změna</small>{change_text}</span>'
                    f'<span class="snow-trend">{sparkline(row.get("history", []))}</span>'
                    f'<span class="snow-time">{measured_html}</span></a>')
        out += '</div></section>'
    out += '</div><div class="snow-empty" id="snow-no-results" hidden><h3>Žádná stanice neodpovídá výběru</h3><p>Zkuste jiné pohoří nebo upravte hledání.</p></div>'
    return out


if __name__ == '__main__':
    main()
