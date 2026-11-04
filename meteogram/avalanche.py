"""Přehled bulletinů Horské služby. Chybějící bulletin nikdy není stupeň 0.

Přenáší strukturované údaje; celý komentář zůstává na originální stránce HS.
Neznámý formát ani starý bulletin se nevydávají za aktuální předpověď.
"""
from datetime import datetime, timezone, timedelta
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from snow import request, iso, parse_time

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'web/data/avalanche.json'
URL = 'https://www.horskasluzba.cz/cz/avalanche-json'
TZ = ZoneInfo('Europe/Prague')
REGIONS = [('04', 'krkonose', 'Krkonoše'), ('06', 'jeseniky', 'Jeseníky')]
LEVELS = {1: 'Nízké', 2: 'Mírné', 3: 'Značné', 4: 'Vysoké', 5: 'Velmi vysoké'}
PROBLEMS = {'new_snow': 'Nový sníh', 'drifting_snow': 'Navátý sníh',
            'old_snow': 'Starý sníh', 'wet_snow': 'Mokrý sníh', 'gliding_snow': 'Klouzavý sníh'}
ASPECTS = {'N': 'S', 'NE': 'SV', 'E': 'V', 'SE': 'JV', 'S': 'J', 'SW': 'JZ', 'W': 'Z', 'NW': 'SZ'}


def degree(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value in ('1', '2', '3', '4', '5'):
        return int(value)
    return value if isinstance(value, (int, float)) and value in LEVELS else None


def collect(now=None, reader=request):
    now = now or datetime.now(timezone.utc)
    raw = json.loads(reader(URL))
    if not isinstance(raw, list):
        raise ValueError('Neznámý formát lavinové předpovědi.')
    reports = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('Neplatný lavinový bulletin.')
        region = str(item.get('region_id', ''))
        if region not in {r[0] for r in REGIONS}:
            continue
        issued = datetime.fromisoformat(item['date_time'])
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=TZ)
        issued = issued.astimezone(timezone.utc)
        if issued > now + timedelta(minutes=5):
            raise ValueError('Lavinový bulletin má budoucí čas vydání.')
        level = degree(item.get('warning_level'))
        if level is None:
            raise ValueError('Neplatný stupeň lavinového nebezpečí.')
        warnings = []
        for warning in item.get('warnings', []):
            if not isinstance(warning, dict):
                raise ValueError('Neplatné rizikové situace.')
            aspects = [ASPECTS[v.strip()] for v in str(warning.get('exposition', '')).split(',') if v.strip() in ASPECTS]
            warnings.append({'name': PROBLEMS.get(warning.get('type'), 'Další riziková situace'),
                             'aspects': aspects})
        report = {'region': region, 'issued_at': iso(issued), 'level': int(level), 'warnings': warnings}
        if region not in reports or report['issued_at'] > reports[region]['issued_at']:
            reports[region] = report
    if raw and not reports:
        raise ValueError('Zdroj neobsahuje žádnou rozpoznanou lavinovou oblast.')
    return {'version': 1, 'retrieved_at': iso(now), 'ok': True, 'reports': list(reports.values())}


def unavailable(now=None):
    return {'version': 1, 'retrieved_at': iso(now or datetime.now(timezone.utc)), 'ok': False, 'reports': []}


def save(snapshot):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
    temporary.replace(OUT)


def main():
    snapshot = collect()
    save(snapshot)
    print(f'Lavinové bulletiny: {len(snapshot["reports"])}.', flush=True)
    return snapshot


def display_time(value):
    dt = parse_time(value).astimezone(TZ)
    return f'{dt.day}. {dt.month}. {dt.year} {dt.hour:02d}:{dt.minute:02d} {"SELČ" if dt.dst() else "SEČ"}'


def render(snapshot, now=None):
    now = now or datetime.now(timezone.utc)
    checked = snapshot.get('retrieved_at') if snapshot else None
    ok = bool(snapshot and snapshot.get('ok'))
    # Starý uložený snapshot sám o sobě nepotvrzuje dostupnost zdroje.
    verified = ok and checked and timedelta(0) <= now - parse_time(checked) < timedelta(hours=2)
    records = {r['region']: r for r in snapshot.get('reports', [])} if verified else {}
    status = 'Zdroj ověřen' if verified else 'Dostupnost předpovědi není ověřena'
    out = f'<div class="snow-updated"><span>{status}</span>'
    if checked:
        out += f'<time datetime="{escape(checked, quote=True)}">{display_time(checked)}</time>'
    today = now.astimezone(TZ).date().isoformat()
    current = sum(1 for report in records.values()
                  if parse_time(report['issued_at']).astimezone(TZ).date().isoformat() == today
                  and parse_time(report['issued_at']) <= now and degree(report.get('level')))
    info = ('Čekáme na aktuální lavinová data Horské služby. Jakmile budou dostupná, zobrazí se zde při aktualizaci přehledu.'
            if verified else 'Dostupnost lavinových dat se nepodařilo ověřit. Aktuální předpověď najdete na webu Horské služby.')
    out += (f'</div><p class="snow-notice ava-infobar" role="status"{" hidden" if current == len(REGIONS) else ""}>'
            f'{info}</p><div class="ava-grid" data-ava-checked="{escape(checked or "", quote=True)}">')
    for region, slug, name in REGIONS:
        report = records.get(region)
        issued = parse_time(report['issued_at']) if report else None
        fresh = bool(issued and issued.astimezone(TZ).date().isoformat() == today and issued <= now)
        level = degree(report.get('level')) if fresh else None
        label = LEVELS[level] + ' nebezpečí' if level else 'Starší bulletin' if report else 'Předpověď není dostupná'
        source = f'https://www.horskasluzba.cz/cz/pocasi-na-horach/lavinova-predpoved/{slug}'
        out += (f'<article class="snow-highlight ava-card" data-ava-day="{issued.astimezone(TZ).date().isoformat() if issued else ""}" data-level="{level or ""}">'
                f'<div class="hc-h"><div><h2>{name}</h2><span>Lavinová oblast</span></div><span class="tag">Horská služba</span></div>'
                '<div class="ava-rating"><strong class="ava-level">' + (str(level) if level else '—') + '</strong>'
                f'<div><b class="ava-label">{label}</b><span class="ava-rating-note">{"Stupeň z pětidílné stupnice" if level else "Stupeň nebezpečí neuvádíme"}</span></div></div>')
        if issued:
            out += f'<p class="ava-issued">Vydáno <time datetime="{escape(report["issued_at"], quote=True)}">{display_time(report["issued_at"])}</time></p>'
        if level and report.get('warnings'):
            out += '<div class="ava-problems"><h3>Rizikové situace a orientace svahů</h3>'
            for warning in report['warnings']:
                out += f'<p><b>{escape(warning["name"])}</b>'
                out += f'<span>{escape(" · ".join(warning["aspects"])) if warning["aspects"] else "Upřesnění v bulletinu HS"}</span></p>'
            out += '</div>'
        else:
            message = ('Bulletin není z dnešního dne. Aktuální situaci ověřte u Horské služby.' if report
                       else 'Pro tuto oblast nyní nemáme dostupný bulletin. Chybějící předpověď neznamená nulové lavinové nebezpečí.')
            out += f'<p class="ava-message">{message}</p>'
        out += (f'<div class="snow-card-foot"><a class="cont" href="{source}" target="_blank" rel="noopener">'
                'Předpověď Horské služby <span aria-hidden="true">↗</span></a></div></article>')
    out += '</div>'
    return out


if __name__ == '__main__':
    main()
