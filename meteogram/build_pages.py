"""Statické stránky pro vyhledávače a AI asistenty: python3 meteogram/build_pages.py [výstup]

Z web/index.html a posledního běhu modelu ve web/data vytvoří (výchozí výstup build/site):
úvod, stránku každé stanice (/praded/), každého pohoří (/krkonose/), O projektu, Ochranu soukromí,
404.html, sitemap.xml, robots.txt a llms.txt. Stránky obsahují titulek, popis, strukturovaná data
a předpověď po dnech přímo v HTML, takže je přečte i robot, který nespouští JavaScript.
Texty pod meteogramem musí odpovídat funkcím stationInfo() a rangeView() v index.html.
"""

from datetime import datetime, timedelta, timezone
from html import escape
import json
import math
from pathlib import Path
import re
import shutil
import sys
import unicodedata
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
SITE = 'https://meteogramy.cz'
TZ = ZoneInfo('Europe/Prague')
WD = ['po', 'út', 'st', 'čt', 'pá', 'so', 'ne']
# stejné pořadí jako RANGES v index.html
RANGES = ['Krkonoše', 'Jizerské hory', 'Ještěd a Kozákov', 'Lužické hory', 'České středohoří', 'Krušné hory',
          'Doupovské hory', 'Slavkovský les', 'Český les', 'Šumava', 'Novohradské hory', 'Brdy', 'Vysočina',
          'Orlické hory', 'Králický Sněžník', 'Hrubý Jeseník', 'Nízký Jeseník', 'Beskydy', 'Javorníky',
          'Hostýnské vrchy', 'Bílé Karpaty']
PAGES = {
    'o-projektu': ('O projektu – Meteogramy.cz',
                   'Odkud jsou data, jak meteogramy ALADIN pro české hory vznikají a jak číst předpověď z modelu.'),
    'ochrana-soukromi': ('Ochrana soukromí – Meteogramy.cz',
                         'Jak Meteogramy.cz nakládají s údaji: bez účtu, bez cookies a bez sledování.'),
}
RESERVED = {'data', 'api', 'assets', *PAGES}
# AI asistenti a jejich vyhledávače, kterým robots.txt výslovně povoluje přístup
AI_BOTS = ['GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'Claude-SearchBot', 'Claude-User',
           'PerplexityBot', 'Perplexity-User', 'Google-Extended', 'Applebot-Extended', 'CCBot']


def slug(text):
    t = unicodedata.normalize('NFD', text)
    t = ''.join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9]+', '-', t).strip('-')


def stations(n):
    return 'horskou stanici' if n == 1 else 'horské stanice' if n < 5 else 'horských stanic'


def js_round(v):
    return math.floor(v + 0.5)


def num(v, d=1):
    return f'{v:.{d}f}'.replace('.', ',').replace('-', '−')


def deg(v):
    return f'{js_round(v)}°'.replace('-', '−')


def gust_color(g):
    return '#30d158' if g < 8 else '#ffd60a' if g < 14 else '#ff9f0a' if g < 20 else '#ff453a' if g < 25 else '#bf1a2f'


def local(iso):
    return datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(TZ)


def tz_name(lt):
    return 'SELČ' if lt.dst() else 'SEČ'


def km(a, b):
    r = math.pi / 180
    x = (b['lon'] - a['lon']) * r * math.cos((a['lat'] + b['lat']) / 2 * r)
    y = (b['lat'] - a['lat']) * r
    return math.hypot(x, y) * 6371


def daily(times, d):
    out = []
    for i, t in enumerate(times):
        lt = local(t)
        key = lt.date()
        if not out or out[-1]['key'] != key:
            out.append({'key': key, 'label': f'{WD[lt.weekday()]} {lt.day}. {lt.month}.', 'from': lt.hour,
                        't': [], 'gust': 0, 'prec': 0, 'snow': 0, 'sun': 0})
        g = out[-1]
        g['t'].append(d['t'][i])
        g['gust'] = max(g['gust'], d['gust'][i])
        g['prec'] += d['prec'][i]
        g['snow'] += d['snow'][i]
        g['sun'] += d['sun'][i] / 60
    return [g for g in out if len(g['t']) >= 3]


def station_info(m, places, series, times, run):
    rows = ''
    if series:
        for g in daily(times, series):
            frm = f' <small>od {g["from"]} h</small>' if g['from'] > 0 else ''
            rows += (f'<tr><td>{g["label"]}{frm}</td><td>{deg(min(g["t"]))} / <span class="tx">{deg(max(g["t"]))}</span></td>'
                     f'<td>{js_round(g["gust"])} m/s</td><td>{"–" if g["prec"] < 0.1 else num(g["prec"]) + " mm"}</td>'
                     f'<td>{"–" if g["snow"] < 0.5 else str(js_round(g["snow"])) + " cm"}</td><td class="hm">{num(g["sun"])} h</td></tr>')
    lt = local(run)
    near = sorted((p for p in places if p['id'] != m['id']), key=lambda p: km(m, p))[:6]
    name, rng = escape(m['name']), escape(m['range'])
    geo = lambda v: f'{v:.4f}'.replace('.', ',')
    spec = [
        ('Stanice', f'ČHMÚ, měří od roku {m["since"]}' if m.get('since') else 'ČHMÚ'),
        ('Pohoří', rng),
        ('Nadmořská výška', f'{m["alt"]} m n. m.'),
        ('Souřadnice', f'{geo(m["lat"])}° s. š., {geo(m["lon"])}° v. d.'),
        ('Bod modelu', f'{m["node"]["alt"]} m n. m., {num(m["node"]["dist"])} km od stanice'),
        ('Model', 'ALADIN CZ, ČHMÚ'),
        ('Předpověď', '3 dny po hodinách, 4× denně'),
        ('Veličiny', 'teplota, vítr a nárazy, srážky, sníh, vlhkost, tlak, oblačnost'),
    ]
    return (
        f'<div><h2>Předpověď po dnech</h2><table class="days"><thead><tr><th>Den</th><th>Teplota</th><th>Nárazy</th>'
        f'<th>Srážky</th><th>Sníh</th><th class="hm">Slunce</th></tr></thead><tbody>{rows}</tbody></table>'
        f'<p class="src">Model ALADIN CZ, běh {lt.day}. {lt.month}. {lt.hour:02d}:00 {tz_name(lt)}. '
        f'Teplota ve výšce uzlu modelu {m["node"]["alt"]} m.</p></div>'
        f'<div><h2>O stanici</h2><dl class="spec">'
        + ''.join(f'<div><dt>{k}</dt><dd>{v}</dd></div>' for k, v in spec)
        + '</dl>'
        f'<h2>Stanice v okolí</h2><nav class="near" aria-label="Stanice v okolí">'
        + ''.join(f'<a href="/{p["id"]}/">{escape(p["name"])} <small>{p["alt"]} m</small></a>' for p in near)
        + f'</nav><a class="more" href="/{slug(m["range"])}/">Všechny stanice: {rng} →</a></div>'
    )


def summary_row(p, d, times):
    """Stejné hodnoty jako summaryRows() v index.html: teď a příštích 24 h."""
    t0 = datetime.fromisoformat(times[0].replace('Z', '+00:00'))
    i0 = max(0, min(len(times) - 1, js_round((datetime.now(timezone.utc) - t0) / timedelta(hours=1))))
    sl = lambda k: d[k][i0:i0 + 24]
    return {'now': d['t'][i0], 'gmax': max(sl('gust')), 'pr': sum(sl('prec')), 'sn': sum(sl('snow'))}


def card(p, r):
    prec = (f'{js_round(r["sn"])} cm sněhu' if r['sn'] >= 0.5 else 'bez srážek' if r['pr'] < 0.1 else f'{num(r["pr"])} mm')
    return (f'<a class="st" href="/{p["id"]}/"><div class="lb">{p["alt"]} m n. m.</div><b>{num(r["now"])}°</b>'
            f'<div class="sx"><em>{escape(p["name"])}</em></div><div class="sx"><i class="gd" style="background:{gust_color(r["gmax"])}"></i>'
            f'{js_round(r["gmax"])} m/s · {prec}</div></a>')


def groups(places):
    order = RANGES + sorted({p['range'] for p in places} - set(RANGES))
    out = [(r, sorted((p for p in places if p['range'] == r), key=lambda p: -p['alt'])) for r in order]
    return [(r, ps) for r, ps in out if ps]


def range_links(places, except_=None):
    return ''.join(f'<a class="rc" href="/{slug(r)}/"><span class="th"><img src="/assets/pohori/{slug(r)}.jpg" alt="" width="960" height="720" '
                   f'loading="lazy" decoding="async"></span><span class="tt"><b>{escape(r)}</b>'
                   f'<small>{len(ps)} {"stanic" if len(ps) >= 5 else "stanice"} · až {ps[0]["alt"]} m</small></span></a>'
                   for r, ps in groups(places) if r != except_)


def fill(html, el_id, inner, attrs=''):
    pattern = re.compile(rf'(<(\w+)[^>]*\bid="{el_id}"[^>]*)>(.*?)(</\2>)', re.S)
    html, n = pattern.subn(lambda m: f'{m.group(1)}{attrs}>{inner}{m.group(4)}', html, count=1)
    assert n == 1, el_id
    return html


def visible(html, show):
    for el_id in ('home', 'range', 'mg', 'sinfo', 'about', 'privacy'):
        html, n = re.subn(rf'id="{el_id}"( hidden)?', f'id="{el_id}"' + ('' if el_id in show else ' hidden'), html, count=1)
        assert n == 1, el_id
    return html


def head(html, title, desc, path, graph):
    url = SITE + path
    jsonld = json.dumps({'@context': 'https://schema.org', '@graph': graph}, ensure_ascii=False).replace('</', '<\\/')
    seo = '\n'.join([
        '<!-- seo -->',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Meteogramy.cz">',
        '<meta property="og:locale" content="cs_CZ">',
        f'<meta property="og:title" content="{escape(title)}">',
        f'<meta property="og:description" content="{escape(desc)}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:image" content="{SITE}/assets/og.png">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<script type="application/ld+json">{jsonld}</script>',
        '<!-- /seo -->',
    ])
    html = re.sub(r'<title>.*?</title>', lambda _: f'<title>{escape(title)}</title>', html, count=1)
    html = re.sub(r'<meta name="description" content="[^"]*">', lambda _: f'<meta name="description" content="{escape(desc)}">', html, count=1)
    html = re.sub(r'<link rel="canonical" href="[^"]*">', lambda _: f'<link rel="canonical" href="{url}">', html, count=1)
    html, n = re.subn(r'<!-- seo -->.*?<!-- /seo -->', lambda _: seo, html, count=1, flags=re.S)
    assert n == 1
    return html


WEBSITE = {'@type': 'WebSite', '@id': f'{SITE}/#website', 'name': 'Meteogramy.cz', 'alternateName': 'Meteogramy',
           'url': f'{SITE}/', 'inLanguage': 'cs',
           'description': 'Meteogramy ALADIN pro horské meteorologické stanice ČHMÚ v Česku.'}


def crumbs(*items):
    return {'@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': i + 1, 'name': name, 'item': SITE + path} for i, (name, path) in enumerate(items)]}


def page(html, path, title, desc, about=None, modified=None, extra=()):
    web_page = {'@type': 'WebPage', '@id': SITE + path, 'url': SITE + path, 'name': title, 'description': desc,
                'inLanguage': 'cs', 'isPartOf': {'@id': f'{SITE}/#website'}}
    if about:
        web_page['about'] = about
    if modified:
        web_page['dateModified'] = modified
    return head(html, title, desc, path, [WEBSITE, web_page, *extra])


def write(out, path, text):
    dest = out / path.strip('/') / 'index.html' if path.endswith('/') else out / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


def not_found():
    return '''<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Stránka nenalezena – Meteogramy.cz</title>
<style>
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f8f8fa; color: #15191f;
  font: 17px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 520px; padding: 24px 16px; text-align: center; }
img { width: 56px; height: 56px; }
h1 { font-size: 34px; letter-spacing: -.02em; margin: 16px 0 8px; }
p { color: #6e6e73; margin: 0 0 24px; }
a.btn { display: inline-block; padding: 11px 20px; border-radius: 12px; background: #1f5fbf; color: #fff; text-decoration: none; font-weight: 600; }
</style>
</head>
<body>
<main>
  <img src="/assets/logo.svg?v=2" alt="">
  <h1>Tahle stránka tu není</h1>
  <p>Odkaz je možná starý nebo v něm je překlep. Meteogramy všech horských stanic najdete na úvodní stránce.</p>
  <a class="btn" href="/">Na úvodní stránku</a>
</main>
</body>
</html>
'''


def main(out=ROOT / 'build' / 'site', data=WEB / 'data'):
    template = (WEB / 'index.html').read_text()
    hidden = 'name="robots" content="noindex' in template
    runs = json.loads((data / 'index.json').read_text())['runs']
    meta = json.loads((data / runs[0] / 'index.json').read_text())
    summary = json.loads((data / runs[0] / 'summary.json').read_text())
    places, run, times = meta['places'], meta['run'], summary['times']
    by_range = groups(places)
    for r, _ in by_range:
        assert slug(r) not in RESERVED | {p['id'] for p in places}, r
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    modified = run
    urls = []

    # úvod
    html = fill(template, 'hranges', range_links(places))
    html = html.replace('<span id="hcount">109</span>', f'<span id="hcount">{len(places)}</span>')
    write(out, '/', page(html, '/', re.search(r'<title>(.*?)</title>', template).group(1),
                         re.search(r'<meta name="description" content="([^"]*)">', template).group(1), modified=modified))
    urls.append('/')

    # pohoří
    for r, ps in by_range:
        path = f'/{slug(r)}/'
        title = f'{r} – meteogramy ALADIN, počasí na horách | Meteogramy.cz'
        desc = (f'Meteogramy ALADIN pro {len(ps)} {stations(len(ps))} ČHMÚ v pohoří {r}: '
                f'hodinová předpověď teploty, větru a nárazů, srážek a sněhu na 3 dny.')
        lead = (f'Hodinová předpověď modelu ALADIN pro {len(ps)} {stations(len(ps))} ČHMÚ '
                f'v pohoří {r}. Teplota, vítr a nárazy, srážky a sníh na tři dny dopředu.')
        html = visible(template, {'range'})
        html = fill(html, 'rtitle', f'{escape(r)} – meteogramy a počasí na horách')
        html = fill(html, 'rlead', escape(lead))
        html = fill(html, 'rlist', ''.join(card(p, summary_row(p, summary['places'][p['id']], times))
                                           for p in ps if p['id'] in summary['places']), f' data-r="{escape(r)}"')
        html = fill(html, 'rmore', range_links(places, r))
        listing = {'@type': 'ItemList', 'name': f'Horské stanice – {r}', 'itemListElement': [
            {'@type': 'ListItem', 'position': i + 1, 'name': p['name'], 'url': f'{SITE}/{p["id"]}/'} for i, p in enumerate(ps)]}
        write(out, path, page(html, path, title, desc, about={'@type': 'Place', 'name': r}, modified=modified,
                              extra=[crumbs(('Meteogramy.cz', '/'), (r, path)), listing]))
        urls.append(path)

    # stanice
    for p in places:
        path = f'/{p["id"]}/'
        title = f'{p["name"]} ({p["alt"]} m) – meteogram ALADIN, předpověď počasí | Meteogramy.cz'
        desc = (f'Meteogram ALADIN pro {p["name"]} ({p["range"]}, {p["alt"]} m n. m.): hodinová předpověď počasí na 3 dny – '
                f'teplota, vítr a nárazy, srážky, sníh, tlak a oblačnost. Data ČHMÚ.')
        since = f' (od {p["since"]})' if p.get('since') else ''
        html = visible(template, {'mg', 'sinfo'})
        html = fill(html, 'title', escape(p['name']))
        html = fill(html, 'sub', f'{escape(p["range"])} · <b>{p["alt"]} m n. m.</b> · stanice ČHMÚ{since}')
        html = fill(html, 'sinfo', station_info(p, places, summary['places'].get(p['id']), times, run))
        place = {'@type': 'Place', '@id': f'{SITE}{path}#misto', 'name': p['name'],
                 'description': f'Meteorologická stanice ČHMÚ, {p["range"]}, {p["alt"]} m n. m.',
                 'geo': {'@type': 'GeoCoordinates', 'latitude': p['lat'], 'longitude': p['lon'], 'elevation': p['alt']},
                 'containedInPlace': {'@type': 'Place', 'name': p['range']}}
        write(out, path, page(html, path, title, desc, about={'@id': place['@id']}, modified=modified,
                              extra=[place, crumbs(('Meteogramy.cz', '/'), (p['range'], f'/{slug(p["range"])}/'), (p['name'], path))]))
        urls.append(path)

    # informační stránky
    for key, (title, desc) in PAGES.items():
        path = f'/{key}/'
        html = visible(template, {'about' if key == 'o-projektu' else 'privacy'})
        write(out, path, page(html, path, title, desc))
        urls.append(path)

    write(out, '404.html', not_found())
    write(out, 'sitemap.xml', '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + ''.join(f'  <url><loc>{SITE}{u}</loc>{"" if u[1:-1] in PAGES else f"<lastmod>{modified}</lastmod>"}</url>\n' for u in urls)
          + '</urlset>\n')
    # dokud má web noindex, roboti ho neprocházejí; po zveřejnění jsou vítaní vyhledávače i AI asistenti
    if hidden:
        robots = '# Web zatím není zveřejněný.\nUser-agent: *\nDisallow: /\n'
    else:
        rules = 'Allow: /\nDisallow: /api/\n'
        robots = ('User-agent: *\n' + rules + '\n# AI asistenti a jejich vyhledávače jsou vítáni\n'
                  + ''.join(f'User-agent: {b}\n' for b in AI_BOTS) + rules + f'\nSitemap: {SITE}/sitemap.xml\n')
    write(out, 'robots.txt', robots)
    lt = local(run)
    write(out, 'llms.txt', '\n'.join([
        '# Meteogramy.cz',
        '',
        f'> Meteogramy ALADIN pro {len(places)} horských meteorologických stanic ČHMÚ v Česku: hodinová předpověď teploty, '
        'větru a nárazů, srážek, sněhu, vlhkosti, tlaku a oblačnosti na 3 dny z numerického modelu ALADIN CZ. '
        'Předpověď se obnovuje čtyřikrát denně. Meteogramy.cz nejsou oficiální službou ČHMÚ.',
        '',
        f'Každá stanice má vlastní stránku s meteogramem a tabulkou předpovědi po dnech (minimální a maximální teplota, '
        f'nejsilnější nárazy větru, srážky, nový sníh, slunce). Aktuální běh modelu: {lt.day}. {lt.month}. {lt.year} '
        f'{lt.hour:02d}:00 {tz_name(lt)}. Zdroj dat: ČHMÚ – otevřená data (https://opendata.chmi.cz/). '
        'Jde o automatickou modelovou předpověď bez zásahu meteorologa; výstrahy vydává ČHMÚ (https://www.chmi.cz/).',
        '',
        '## Pohoří',
        '',
        *[f'- [{r}]({SITE}/{slug(r)}/): {len(ps)} {"stanice" if len(ps) < 5 else "stanic"}, nejvyšší {ps[0]["name"]} ({ps[0]["alt"]} m)'
          for r, ps in by_range],
        '',
        '## Stanice',
        '',
        *[f'- [{p["name"]}]({SITE}/{p["id"]}/): {r}, {p["alt"]} m n. m.' for r, ps in by_range for p in ps],
        '',
        '## Další',
        '',
        f'- [O projektu]({SITE}/o-projektu/): odkud jsou data a jak meteogramy vznikají',
        f'- [Ochrana soukromí]({SITE}/ochrana-soukromi/)',
        '',
    ]))
    print(f'Vygenerováno {len(urls)} stránek do {out}.', flush=True)
    return out


if __name__ == '__main__':
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'build' / 'site')
