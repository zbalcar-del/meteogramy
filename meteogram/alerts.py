"""Aktuální celostátní protokol CAP ČHMÚ pro kartu na úvodní stránce."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from snow import iso, parse_time, request

OUT = Path(__file__).resolve().parent.parent / 'web/data/alerts.json'
URL = 'https://vystrahy-cr.chmi.cz/data2/XOCZ50_OKPR.xml'
NS = '{urn:oasis:names:tc:emergency:cap:1.2}'
LEVELS = {'Unknown': 0, 'Moderate': 1, 'Severe': 2, 'Extreme': 3}


def parse(raw, now=None):
    now = now or datetime.now(timezone.utc)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Nepovolené deklarace XML.')
    root = ET.fromstring(raw)
    def text(node, key):
        return (node.findtext(NS + key) or '').strip()
    if (root.tag != NS + 'alert' or text(root, 'status') != 'Actual'
            or text(root, 'scope') != 'Public' or text(root, 'sender') != 'chmi@chmi.cz'
            or text(root, 'msgType') not in ('Alert', 'Update')):
        raise ValueError('Neplatný veřejný protokol ČHMÚ.')
    issued = parse_time(text(root, 'sent'))
    if issued > now + timedelta(minutes=5):
        raise ValueError('Protokol má budoucí čas vydání.')
    infos = [i for i in root.findall(NS + 'info') if text(i, 'language').lower() in ('cs', 'cs-cz')]
    if not infos:
        raise ValueError('Protokol neobsahuje české informace.')
    warnings = {}
    for info in infos:
        severity, certainty = text(info, 'severity'), text(info, 'certainty')
        if severity == 'Minor' or certainty == 'Unlikely':
            continue  # Zelené bloky CAP výslovně označují stav bez výstrahy.
        if severity not in LEVELS or certainty not in ('Observed', 'Likely', 'Possible', 'Unknown'):
            raise ValueError('Neznámá závažnost nebo pravděpodobnost výstrahy.')
        effective = parse_time(text(info, 'effective')) if text(info, 'effective') else issued
        if effective > now:
            raise ValueError('Protokol ještě nevstoupil v platnost.')
        onset = parse_time(text(info, 'onset')) if text(info, 'onset') else effective
        end = parse_time(text(info, 'expires')) if text(info, 'expires') else None
        if end and end <= now:
            continue
        event = text(info, 'event')
        areas = [text(a, 'areaDesc') for a in info.findall(NS + 'area') if text(a, 'areaDesc')]
        if not event or not areas:
            raise ValueError('Výstraha nemá název nebo územní platnost.')
        key = (event, severity, certainty, iso(onset), iso(end) if end else None)
        warning = warnings.setdefault(key, {
            'event': event, 'level': LEVELS[severity], 'certainty': certainty,
            'onset': iso(onset), 'expires': iso(end) if end else None, 'areas': [],
        })
        warning['areas'] = sorted(set(warning['areas']) | set(areas))
    return {'version': 1, 'ok': True, 'retrieved_at': iso(now), 'issued_at': iso(issued),
            'warnings': sorted(warnings.values(), key=lambda w: (-w['level'], w['onset'], w['event']))}


def unavailable():
    return {'version': 1, 'ok': False, 'retrieved_at': iso(datetime.now(timezone.utc)), 'warnings': []}


def save(snapshot):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
    temporary.replace(OUT)


def main():
    snapshot = parse(request(URL))
    save(snapshot)
    print(f'Výstrahy ČHMÚ: {len(snapshot["warnings"])}.', flush=True)
    return snapshot


if __name__ == '__main__':
    main()
