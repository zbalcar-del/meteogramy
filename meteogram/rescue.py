"""Užitečné zprávy Horské služby ČR pro kartu na úvodní stránce.

Bere jen RSS horskasluzba.cz a striktně propouští pouze zprávy o pátrání, zásazích, úrazech
a nebezpečí či podmínkách na horách. Školení, závody, kynologie, prodej majetku apod. vypadnou.
"""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from snow import iso, request

OUT = Path(__file__).resolve().parent.parent / 'web/data/rescue.json'
URL = 'https://www.horskasluzba.cz/cz/rss'
SITE = 'https://www.horskasluzba.cz/'
MAX_AGE_DAYS = 30
# pátrání obsahuje jméno hledané osoby a rychle zastará – ukazujeme ho jen týden
SEARCH_MAX_AGE_DAYS = 7
SEARCH = re.compile(r'pohřešov|pátrá|hledá|hledat', re.IGNORECASE)
MAX_ITEMS = 3
SUMMARY_CHARS = 200
# Zpráva projde, jen když titulek nebo úvod obsahuje něco z toho (kmeny slov, bez ohledu na velikost písmen).
USEFUL = re.compile(
    r'pohřešov|pátrá|hledá|hledat|zásah|úraz|zranil|zraněn|poraněn|\bpád|spadl|zřítil|lavin|varov|nebezpe|'
    r'náledí|kluzk|vichřic|silný vítr|uzavř|uzavír|podchlaz|zabloudil|nehod|zemřel|tragick',
    re.IGNORECASE)
# Obecná slova (podmínky, bezpečnost, mlha) se v textech HS objevují i u vybavení a školení – platí jen v titulku.
USEFUL_IN_TITLE = re.compile(r'podmínk|bezpečn|mlh', re.IGNORECASE)
# …a zároveň titulek nesmí být o interním dění Horské služby.
NOT_FOR_VISITORS = re.compile(
    r'cvičen|školen|kurz|závod|memoriál|soutěž|zkoušk|prodej|výběrov|icar|dokument|kynolog|psovod|galeri|'
    r'časopis|výročí|ocenění|poděkován',
    re.IGNORECASE)


def clean(html):
    text = unescape(re.sub(r'<[^>]+>', ' ', html or ''))
    return re.sub(r'\s+', ' ', text).strip()


def shorten(text, limit=SUMMARY_CHARS):
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0].rstrip(',;:–- ')
    return cut + '…'


def useful(title, summary):
    # striktně: rozhoduje titulek a první věta, ne zmínky hlouběji v textu
    first = re.split(r'(?<=[.!?])\s', summary, maxsplit=1)[0]
    relevant = USEFUL.search(f'{title} {first}') or USEFUL_IN_TITLE.search(title)
    return bool(relevant) and not NOT_FOR_VISITORS.search(title)


def parse(raw, now=None):
    now = now or datetime.now(timezone.utc)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Nepovolené deklarace XML.')
    channel = ET.fromstring(raw).find('channel')
    if channel is None:
        raise ValueError('RSS Horské služby nemá kanál.')
    items = []
    for item in channel.findall('item'):
        title = clean(item.findtext('title'))
        link = (item.findtext('link') or '').strip()
        try:
            published = parsedate_to_datetime(item.findtext('pubDate') or '').astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if not title or not link.startswith(SITE) or '/video-a-foto-galerie/' in link or '/kynologie/' in link:
            continue
        if published > now + timedelta(hours=1) or now - published > timedelta(days=MAX_AGE_DAYS):
            continue
        summary = clean(item.findtext('description'))
        if not useful(title, summary):
            continue
        if SEARCH.search(title) and now - published > timedelta(days=SEARCH_MAX_AGE_DAYS):
            continue
        items.append({'title': title, 'published': iso(published), 'summary': shorten(summary), 'url': link})
    items.sort(key=lambda i: i['published'], reverse=True)
    return {'version': 1, 'ok': True, 'retrieved_at': iso(now), 'items': items[:MAX_ITEMS]}


def save(snapshot):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
    temporary.replace(OUT)


def main():
    snapshot = parse(request(URL))
    save(snapshot)
    print(f'Horská služba: {len(snapshot["items"])} užitečných zpráv.', flush=True)
    return snapshot


if __name__ == '__main__':
    main()
