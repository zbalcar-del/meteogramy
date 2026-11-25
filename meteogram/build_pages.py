"""Statické stránky pro vyhledávače a AI asistenty: python3 meteogram/build_pages.py [výstup]

Z web/index.html a posledního běhu modelu ve web/data vytvoří (výchozí výstup build/site):
úvod, stránku každé stanice a lyžařského střediska (/praded/), každého pohoří (/krkonose/), O projektu, Ochranu soukromí,
404.html, sitemap.xml, robots.txt a llms.txt. Stránky obsahují titulek, popis, strukturovaná data
a předpověď po dnech přímo v HTML, takže je přečte i robot, který nespouští JavaScript.
Texty pod meteogramem musí odpovídat funkcím stationInfo() a rangeView() v index.html.
"""

from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import json
import math
from pathlib import Path
import re
import shutil
import snow
import avalanche
import sys
import unicodedata
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
SITE = 'https://meteogramy.cz'
TZ = ZoneInfo('Europe/Prague')
WD = ['po', 'út', 'st', 'čt', 'pá', 'so', 'ne']
PAGES = {
    'o-projektu': ('O projektu – Meteogramy.cz',
                   'Odkud jsou data, jak meteogramy ALADIN pro české hory vznikají a jak číst předpověď z modelu.'),
    'aladin': ('Meteogramy ALADIN – předpověď modelu ALADIN pro české hory | Meteogramy.cz',
               'Meteogramy ALADIN v klasické podobě: co je model ALADIN ČHMÚ, jak číst meteogram a kde najdete hodinovou předpověď ALADIN pro horské stanice a lyžařská střediska.'),
    'ochrana-soukromi': ('Ochrana soukromí – Meteogramy.cz',
                         'Jak Meteogramy.cz nakládají s údaji a cookies. Web používáte bez účtu; návštěvnost měříme pomocí Google Analytics jen s vaším souhlasem.'),
}
# hovorový název pohoří, jak ho lidé hledají (jen do titulku); stejně jako RANGE_ALSO v index.html
RANGE_ALSO = {'Hrubý Jeseník': 'Jeseníky'}
SNOW_PATH = '/snehove-zpravodajstvi/'
SNOW_TITLE = 'Sněhové zpravodajství – výška sněhu na horách | Meteogramy.cz'
SNOW_DESC = 'Naměřená výška sněhu na českých horách podle ČHMÚ. Denní změna pokrývky, vývoj a čas posledního měření pro horské stanice podle pohoří.'
AVALANCHE_PATH = '/lavinova-predpoved/'
AVALANCHE_TITLE = 'Lavinová předpověď – Krkonoše a Jeseníky | Meteogramy.cz'
AVALANCHE_DESC = 'Lavinová předpověď Horské služby ČR pro Krkonoše a Jeseníky. Stupeň nebezpečí, rizikové situace, čas vydání a odkazy na oficiální bulletiny.'
WINTERS_PATH = '/zimy-na-horach/'
WINTERS_TITLE = 'Zimy na horách – Krkonoše a Hrubý Jeseník | Meteogramy.cz'
WINTERS_DESC = 'Výška sněhu na horských stanicích ČHMÚ v Krkonoších a Hrubém Jeseníku za všechny zimy od konce 19. století.'
RESERVED = {'data', 'api', 'assets', 'sprava-webu-meteo', SNOW_PATH.strip('/'), AVALANCHE_PATH.strip('/'), WINTERS_PATH.strip('/'), *PAGES}
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


def is_resort(p):
    return p.get('kind') == 'resort'


def word(m):
    """Stejně jako word() v index.html: o čem mluví texty u místa."""
    if is_resort(m):
        return ('vrchol', 'vrcholu') if m.get('top') else ('středisko', 'střediska')
    return ('stanice', 'stanice')


def resorts_word(n):
    return 'lyžařské středisko' if n == 1 else 'lyžařská střediska' if n < 5 else 'lyžařských středisek'


STATION_NOTE = ('Uvedená výška je nadmořská výška meteorologické stanice ČHMÚ, ne vrcholu hory. '
                'Stanice často stojí pod vrcholem, v sedle nebo u horské chaty.')
TOP_NOTE = ('Uvedená výška a poloha patří vrcholu areálu: nejvýše položené horní stanici vleku nebo lanovky '
            'podle OpenStreetMap. Pro tento bod počítáme předpověď.')
RESORT_NOTE = 'Poloha a výška střediska podle mapy lyžařských středisek ČHMÚ.'


def site_links(links, places):
    """Střediska s vrcholem u stanice; dvě části téhož areálu („Klínovec, jih“) jednou. Stejně jako siteLinks() v index.html."""
    ids = {p['id'] for p in places}
    links = [l for l in links if l['same'] in ids]
    return [l for l in links if not any(o is not l and o['same'] == l['same'] and l['name'].startswith(o['name'] + ',') for o in links)]


def link_href(l, station):
    """Stejně jako linkHref() v index.html: /kvilda/?stredisko=nove-hute, stejnojmenné středisko jen /kvilda/."""
    return f'/{l["same"]}/' if l['name'] == station['name'] else f'/{l["same"]}/?stredisko={slug(l["name"])}'


def station_info(m, places, series, times, run, range_pages=(), links=()):
    rows = ''
    if series:
        for g in daily(times, series):
            frm = f' <small>od {g["from"]} h</small>' if g['from'] > 0 else ''
            rows += (f'<tr><td>{g["label"]}{frm}</td><td>{deg(min(g["t"]))} / <span class="tx">{deg(max(g["t"]))}</span></td>'
                     f'<td>{js_round(g["gust"])} m/s</td><td>{"–" if g["prec"] < 0.1 else num(g["prec"]) + " mm"}</td>'
                     f'<td>{"–" if g["snow"] < 0.5 else str(js_round(g["snow"])) + " cm"}</td><td class="hm">{num(g["sun"])} h</td></tr>')
    lt = local(run)
    resort, (_, gen) = is_resort(m), word(m)
    near = sorted((p for p in places if p['id'] != m['id'] and is_resort(p) == resort), key=lambda p: km(m, p))[:6]
    station = resort and min((p for p in places if not is_resort(p)), key=lambda p: km(m, p))
    areas = [] if resort else [escape(l['name']) for l in site_links(links, places) if l['same'] == m['id']]
    name, rng = escape(m['name']), escape(m['range'])
    geo = lambda v: f'{v:.4f}'.replace('.', ',')
    spec = [
        ('Středisko', 'vrchol areálu – nejvýš položená horní stanice vleku nebo lanovky' if m.get('top') else 'lyžařské středisko')
        if resort else ('Stanice', f'ČHMÚ, měří od roku {m["since"]}' if m.get('since') else 'ČHMÚ'),
        *([('Lyžařská střediska' if len(areas) > 1 else 'Lyžařské středisko',
            f'{", ".join(areas)} – předpověď platí i pro {"vrcholy areálů" if len(areas) > 1 else "vrchol areálu"}')] if areas else []),
        *([('Pohoří', rng)] if m['range'] != 'Ostatní' else []),
        ('Nadmořská výška', f'{m["alt"]} m n. m.'),
        ('Souřadnice', f'{geo(m["lat"])}° s. š., {geo(m["lon"])}° v. d.'),
        ('Bod modelu', f'{m["node"]["alt"]} m n. m., {num(m["node"]["dist"])} km od {gen}'),
        *([('Nejbližší stanice ČHMÚ', f'<a href="/{station["id"]}/">{escape(station["name"])}</a>, {num(km(m, station))} km')] if station else []),
        ('Model', 'ALADIN CZ, ČHMÚ'),
        ('Předpověď', '3 dny po hodinách, 4× denně'),
        ('Veličiny', 'teplota, vítr a nárazy, srážky, sníh, vlhkost, tlak, oblačnost'),
    ]
    return (
        f'<div><h2>Předpověď po dnech</h2><table class="days"><thead><tr><th>Den</th><th>Teplota</th><th>Nárazy</th>'
        f'<th>Srážky</th><th>Sníh</th><th class="hm">Slunce</th></tr></thead><tbody>{rows}</tbody></table>'
        f'<p class="src">Model ALADIN CZ, běh {lt.day}. {lt.month}. {lt.hour:02d}:00 {tz_name(lt)}. '
        f'Teplota ve výšce uzlu modelu {m["node"]["alt"]} m.</p></div>'
        f'<div><h2>{"O středisku" if resort else "O stanici"}</h2><dl class="spec">'
        + ''.join(f'<div><dt>{k}</dt><dd>{v}</dd></div>' for k, v in spec)
        + '</dl>'
        f'<h2>{"Střediska" if resort else "Stanice"} v okolí</h2><nav class="near" aria-label="{"Střediska" if resort else "Stanice"} v okolí">'
        + ''.join(f'<a href="/{p["id"]}/">{escape(p["name"])} <small>{p["alt"]} m</small></a>' for p in near)
        + '</nav>'
        + (f'<a class="more" href="/{slug(m["range"])}/">{"Střediska a stanice" if resort else "Všechny stanice"}: {rng} →</a>'
           if m['range'] in range_pages else '')
        + '</div>'
    )


def summary_row(p, d, times):
    """Stejné hodnoty jako summaryRows() v index.html: teď a příštích 24 h."""
    t0 = datetime.fromisoformat(times[0].replace('Z', '+00:00'))
    i0 = max(0, min(len(times) - 1, js_round((datetime.now(timezone.utc) - t0) / timedelta(hours=1))))
    sl = lambda k: d[k][i0:i0 + 24]
    return {'now': d['t'][i0], 'gmax': max(sl('gust')), 'pr': sum(sl('prec')), 'sn': sum(sl('snow'))}


def card(p, r):
    prec = (f'{js_round(r["sn"])} cm sněhu' if r['sn'] >= 0.5 else 'bez srážek' if r['pr'] < 0.1 else f'{num(r["pr"])} mm')
    href = p.get('href') or f'/{p["id"]}/'
    return (f'<a class="st" href="{href}"><div class="lb">{p["alt"]} m n. m.</div><b>{num(r["now"])}°</b>'
            f'<div class="sx"><em>{escape(p["name"])}</em></div><div class="sx"><i class="gd" style="background:{gust_color(r["gmax"])}"></i>'
            f'{js_round(r["gmax"])} m/s · {prec}</div></a>')


def groups(places):
    """Pohoří od toho s nejvyšší stanicí, ve skupině stanice od nejvyšší (stejně jako groups() v index.html)."""
    out = [(r, sorted((p for p in places if p['range'] == r), key=lambda p: -p['alt'])) for r in {p['range'] for p in places}]
    return sorted(out, key=lambda g: -g[1][0]['alt'])


def range_links(places, except_=None):
    return ''.join(f'<a class="rc" href="/{slug(r)}/"><span class="th"><img src="/assets/pohori/{slug(r)}.svg?v=7" alt="" width="400" height="300" '
                   f'loading="lazy" decoding="async"></span><span class="tt"><b>{escape(r)}</b>'
                   f'<small>{len(ps)} {"stanic" if len(ps) >= 5 else "stanice"} · až {ps[0]["alt"]} m</small></span></a>'
                   for r, ps in groups(places) if r != except_)


def fill(html, el_id, inner, attrs=''):
    pattern = re.compile(rf'(<(\w+)[^>]*\bid="{el_id}"[^>]*)>(.*?)(</\2>)', re.S)
    html, n = pattern.subn(lambda m: f'{m.group(1)}{attrs}>{inner}{m.group(4)}', html, count=1)
    assert n == 1, el_id
    return html


def visible(html, show):
    for el_id in ('home', 'range', 'mg', 'sinfo', 'stnotes', 'about', 'aladin', 'privacy', 'snow', 'avalanche', 'winters'):
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
        f'<meta property="og:image" content="{SITE}/assets/og-card-hd.jpg">',
        '<meta property="og:image:width" content="2400">',
        '<meta property="og:image:height" content="1260">',
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
           'description': 'Meteogramy ALADIN pro horské meteorologické stanice ČHMÚ a lyžařská střediska v Česku.'}


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


def shared_assets(out, html):
    """Hlavní styl a skript z index.html přesune do sdílených souborů /assets/app.<otisk>.css a .js.
    Jsou stejné na všech stránkách, prohlížeč je stáhne jednou a po každém běhu modelu se nenahrávají znovu
    se stovkami stránek. Otisk obsahu v názvu: hosting drží skripty v mezipaměti 7 dní a stránka tak vždy
    dostane soubor, se kterým vznikla (staré soubory na hostingu zůstávají, nasazení nic nemaže)."""
    for tag, ext, ref in (('style', 'css', '<link rel="stylesheet" href="/assets/{}">'),
                          ('script', 'js', '<script src="/assets/{}"></script>')):
        blocks = re.findall(rf'<{tag}>.*?</{tag}>', html, re.S)
        assert len(blocks) == 1, tag
        body = blocks[0][len(tag) + 2:-len(tag) - 3]
        name = f'app.{hashlib.sha256(body.encode()).hexdigest()[:12]}.{ext}'
        dest = out / 'assets' / name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body)
        html = html.replace(blocks[0], ref.format(name), 1)
    return html


def write(out, path, text):
    if path.endswith('/'):
        text = shared_assets(out, text)
    dest = out / path.strip('/') / 'index.html' if path.endswith('/') else out / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


def write_snow_page(out, data):
    try:
        snapshot = json.loads((data / 'snow.json').read_text())
    except (OSError, ValueError):
        snapshot = None
    fragment = snow.render(snapshot)
    html = visible((WEB / 'index.html').read_text(), {'snow'})
    html = fill(html, 'snow-body', fragment)
    html = html.replace('class="snow-nav"', 'class="snow-nav" aria-current="page"', 1)
    modified = snapshot.get('retrieved_at') if snapshot else None
    write(out, SNOW_PATH, page(html, SNOW_PATH, SNOW_TITLE, SNOW_DESC, modified=modified,
                              extra=[crumbs(('Meteogramy.cz', '/'), ('Sněhové zpravodajství', SNOW_PATH))]))
    write(out, 'data/snow-fragment.html', fragment)
    return modified


def write_avalanche_page(out, data):
    try:
        snapshot = json.loads((data / 'avalanche.json').read_text())
    except (OSError, ValueError):
        snapshot = None
    fragment = avalanche.render(snapshot)
    html = visible((WEB / 'index.html').read_text(), {'avalanche'})
    html = fill(html, 'avalanche-body', fragment)
    html = html.replace('class="snow-nav avalanche-nav"', 'class="snow-nav avalanche-nav" aria-current="page"', 1)
    modified = snapshot.get('retrieved_at') if snapshot else None
    write(out, AVALANCHE_PATH, page(html, AVALANCHE_PATH, AVALANCHE_TITLE, AVALANCHE_DESC, modified=modified,
                                  extra=[crumbs(('Meteogramy.cz', '/'), ('Lavinová předpověď', AVALANCHE_PATH))]))
    write(out, 'data/avalanche-fragment.html', fragment)
    return modified


def write_winters_page(out, modified=None):
    html = visible((WEB / 'index.html').read_text(), {'winters'})
    html = html.replace('class="snow-nav winters-nav"', 'class="snow-nav winters-nav" aria-current="page"', 1)
    write(out, WINTERS_PATH, page(html, WINTERS_PATH, WINTERS_TITLE, WINTERS_DESC, modified=modified,
                                  extra=[crumbs(('Meteogramy.cz', '/'), ('Zimy na horách', WINTERS_PATH))]))


def not_found():
    return '''<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Stránka nenalezena – Meteogramy.cz</title>
<link rel="icon" href="/favicon.ico?v=4" sizes="48x48">
<link rel="icon" href="/favicon.svg?v=4" type="image/svg+xml">
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
  <img src="/assets/logo.png?v=4" alt="">
  <h1>Tahle stránka tu není</h1>
  <p>Odkaz je možná starý nebo v něm je překlep. Meteogramy horských stanic a lyžařských středisek najdete na úvodní stránce.</p>
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
    stations_ = [p for p in places if not is_resort(p)]
    resorts = [p for p in places if is_resort(p)]
    by_range = groups(stations_)
    range_pages = {r for r, _ in by_range}
    for r, _ in by_range:
        assert slug(r) not in RESERVED | {p['id'] for p in places}, r
    assert not {p['id'] for p in places} & RESERVED
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    modified = run
    urls = []

    # úvod
    # počty míst v popisech, úvodu a O projektu podle aktuálního seznamu
    # běh spočítaný před přidáním středisek je ještě nemá: počet ze seznamu středisek (build_resorts.py)
    resorts_file = ROOT / 'meteogram' / 'resorts.json'
    n_resorts = len(resorts) or (sum('same' not in r for r in json.loads(resorts_file.read_text())) if resorts_file.exists() else 0)
    for old, new in (('109 horských stanic ČHMÚ a 123 lyžařských středisek', f'{len(stations_)} horských stanic ČHMÚ a {n_resorts} lyžařských středisek'),
                     ('<span id="hcount">109</span>', f'<span id="hcount">{len(stations_)}</span>'),
                     ('<span id="rcount">123</span>', f'<span id="rcount">{n_resorts}</span>'),
                     ('<b id="fstations">109</b>', f'<b id="fstations">{len(stations_)}</b>'),
                     ('<b id="fresorts">123</b>', f'<b id="fresorts">{n_resorts}</b>')):
        assert old in template, old
        template = template.replace(old, new)
    html = fill(template, 'hranges', range_links(stations_))
    write(out, '/', page(html, '/', re.search(r'<title>(.*?)</title>', template).group(1),
                         re.search(r'<meta name="description" content="([^"]*)">', template).group(1), modified=modified))
    urls.append('/')

    # pohoří
    by_id = {p['id']: p for p in places}
    links = site_links(meta.get('links', []), places)
    for r, ps in by_range:
        path = f'/{slug(r)}/'
        rs = sorted((p for p in resorts if p['range'] == r), key=lambda p: -p['alt'])
        # střediska s vrcholem u stanice (Ramzová na Šeráku): karta se souhrnem stanice a odkazem na středisko
        rs_all = sorted(rs + [{**by_id[l['same']], 'name': l['name'], 'href': link_href(l, by_id[l['same']])}
                              for l in links if l['range'] == r], key=lambda p: -p['alt'])
        also = f' a {len(rs_all)} {resorts_word(len(rs_all))}' if rs_all else ''
        # musí odpovídat rangeView() v index.html
        title = f'Počasí {r}{f" ({RANGE_ALSO[r]})" if r in RANGE_ALSO else ""} – meteogramy ALADIN | Meteogramy.cz'
        desc = (f'Počasí {r} na 3 dny po hodinách: meteogramy ALADIN pro {len(ps)} {stations(len(ps))} ČHMÚ{also} – '
                f'teplota, vítr a nárazy, srážky a sníh.')
        lead = (f'Hodinová předpověď modelu ALADIN pro {len(ps)} {stations(len(ps))} ČHMÚ{also} '
                f'v pohoří {r}. Teplota, vítr a nárazy, srážky a sníh na tři dny dopředu.')
        html = visible(template, {'range'})
        html = fill(html, 'rtitle', f'Počasí {escape(r)} – meteogramy ALADIN')
        html = fill(html, 'rlead', escape(lead))
        html = html.replace('<img id="rimg" src="" alt=""', f'<img id="rimg" src="/assets/pohori/{slug(r)}-siroka.svg?v=7" '
                            f'alt="Vrstevnice v okolí nejvyšší stanice: {escape(ps[0]["name"])}"', 1)
        html = fill(html, 'rlist', ''.join(card(p, summary_row(p, summary['places'][p['id']], times))
                                           for p in ps if p['id'] in summary['places']), f' data-r="{escape(r)}" data-run="{runs[0]}"')
        if rs_all:
            html = html.replace('<div class="sech" id="rres-h" hidden>', '<div class="sech" id="rres-h">', 1)
            html = fill(html, 'rres', ''.join(card(p, summary_row(p, summary['places'][p['id']], times))
                                              for p in rs_all if p['id'] in summary['places']))
            html = html.replace('<div class="hl" id="rres" hidden>', '<div class="hl" id="rres">', 1)
        html = fill(html, 'rmore', range_links(stations_, r))
        listing = {'@type': 'ItemList', 'name': f'Horské stanice a lyžařská střediska – {r}', 'itemListElement': [
            {'@type': 'ListItem', 'position': i + 1, 'name': p['name'], 'url': f'{SITE}/{p["id"]}/'} for i, p in enumerate(ps + rs)]}
        write(out, path, page(html, path, title, desc, about={'@type': 'Place', 'name': r}, modified=modified,
                              extra=[crumbs(('Meteogramy.cz', '/'), (r, path)), listing]))
        urls.append(path)

    # stanice a lyžařská střediska
    for p in places:
        path = f'/{p["id"]}/'
        resort = is_resort(p)
        rng = p['range'] if p['range'] != 'Ostatní' else ''
        where = f'{rng}, {p["alt"]} m n. m.' if rng else f'{p["alt"]} m n. m.'
        if resort:
            title = f'{p["name"]} ({p["alt"]} m) – lyžařské středisko, meteogram ALADIN | Meteogramy.cz'
            desc = (f'Předpověď počasí pro {"vrchol střediska" if p.get("top") else "lyžařské středisko"} {p["name"]} ({where}): '
                    f'meteogram ALADIN na 3 dny po hodinách – teplota, vítr a nárazy, srážky, nový sníh a oblačnost. Data ČHMÚ.')
            kind = 'vrchol lyžařského střediska' if p.get('top') else 'lyžařské středisko'
            note = TOP_NOTE if p.get('top') else RESORT_NOTE
        else:
            title = f'{p["name"]} ({p["alt"]} m) – meteogram ALADIN, předpověď počasí | Meteogramy.cz'
            desc = (f'Meteogram ALADIN pro {p["name"]} ({p["range"]}, {p["alt"]} m n. m.): hodinová předpověď počasí na 3 dny – '
                    f'teplota, vítr a nárazy, srážky, sníh, tlak a oblačnost. Data ČHMÚ.')
            kind = 'stanice ČHMÚ' + (f' (od {p["since"]})' if p.get('since') else '')
            note = STATION_NOTE
        html = visible(template, {'mg', 'sinfo', 'stnotes'})
        html = fill(html, 'title', escape(p['name']))
        html = fill(html, 'sub', f'{escape(rng) + " · " if rng else ""}<b>{p["alt"]} m n. m.</b><a class="fn" href="#pozn-vysky-st" aria-label="Poznámka 1">1</a> · {kind}')
        html = fill(html, 'pozn-vysky-st', escape(note))
        html = fill(html, 'corr-lab', f'Teplota přepočtená na výšku {word(p)[1]}')
        html = fill(html, 'sinfo', station_info(p, places, summary['places'].get(p['id']), times, run, range_pages, meta.get('links', [])))
        place = {'@type': 'Place', '@id': f'{SITE}{path}#misto', 'name': p['name'],
                 'description': (f'Lyžařské středisko, {where}' if resort else f'Meteorologická stanice ČHMÚ, {p["range"]}, {p["alt"]} m n. m.'),
                 'geo': {'@type': 'GeoCoordinates', 'latitude': p['lat'], 'longitude': p['lon'], 'elevation': p['alt']}}
        if rng:
            place['containedInPlace'] = {'@type': 'Place', 'name': rng}
        trail = [('Meteogramy.cz', '/')] + ([(p['range'], f'/{slug(p["range"])}/')] if p['range'] in range_pages else []) + [(p['name'], path)]
        write(out, path, page(html, path, title, desc, about={'@id': place['@id']}, modified=modified,
                              extra=[place, crumbs(*trail)]))
        urls.append(path)

    # informační stránky
    for key, (title, desc) in PAGES.items():
        path = f'/{key}/'
        html = visible(template, {{'o-projektu': 'about', 'aladin': 'aladin'}.get(key, 'privacy')})
        write(out, path, page(html, path, title, desc))
        urls.append(path)

    snow_modified = write_snow_page(out, data)
    urls.append(SNOW_PATH)
    avalanche_modified = write_avalanche_page(out, data)
    write_winters_page(out, modified)
    urls.append(AVALANCHE_PATH)
    urls.append(WINTERS_PATH)
    write(out, '404.html', not_found())
    lastmods = {u: snow_modified or modified if u == SNOW_PATH else modified for u in urls}
    lastmods[AVALANCHE_PATH] = avalanche_modified or modified
    write(out, 'sitemap.xml', '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + ''.join(f'  <url><loc>{SITE}{u}</loc>{"" if u[1:-1] in PAGES else f"<lastmod>{lastmods[u]}</lastmod>"}</url>\n' for u in urls)
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
        f'> Meteogramy ALADIN pro {len(stations_)} horských meteorologických stanic ČHMÚ a {len(resorts)} lyžařských středisek v Česku: hodinová předpověď teploty, '
        'větru a nárazů, srážek, sněhu, vlhkosti, tlaku a oblačnosti na 3 dny z numerického modelu ALADIN CZ. '
        'Předpověď se obnovuje čtyřikrát denně. Meteogramy.cz nejsou oficiální službou ČHMÚ.',
        '',
        f'Každá stanice i lyžařské středisko má vlastní stránku s meteogramem a tabulkou předpovědi po dnech (minimální a maximální teplota, '
        f'nejsilnější nárazy větru, srážky, nový sníh, slunce). Aktuální běh modelu: {lt.day}. {lt.month}. {lt.year} '
        f'{lt.hour:02d}:00 {tz_name(lt)}. Zdroj dat: ČHMÚ – otevřená data (https://opendata.chmi.cz/), licence CC BY 4.0. '
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
        '## Lyžařská střediska',
        '',
        'Předpověď pro vrchol areálu (nejvýše položenou horní stanici vleku nebo lanovky).',
        '',
        *[f'- [{p["name"]}]({SITE}/{p["id"]}/): {p["range"] + ", " if p["range"] != "Ostatní" else ""}{p["alt"]} m n. m.'
          for r, ps in groups(resorts) for p in ps],
        '',
        '## Další',
        '',
        f'- [Lavinová předpověď]({SITE}{AVALANCHE_PATH}): bulletiny Horské služby pro Krkonoše a Jeseníky',
        f'- [Sněhové zpravodajství]({SITE}{SNOW_PATH}): naměřená výška sněhu a denní změna pokrývky, ČHMÚ',
        f'- [Zimy na horách]({SITE}{WINTERS_PATH}): výška sněhu v Krkonoších a Hrubém Jeseníku za všechny zimy od konce 19. století, ČHMÚ',
        f'- [O projektu]({SITE}/o-projektu/): odkud jsou data a jak meteogramy vznikají',
        f'- [Meteogramy ALADIN]({SITE}/aladin/): co je model ALADIN ČHMÚ a jak číst meteogram',
        f'- [Ochrana soukromí]({SITE}/ochrana-soukromi/)',
        '',
    ]))
    print(f'Vygenerováno {len(urls)} stránek do {out}.', flush=True)
    return out


if __name__ == '__main__':
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'build' / 'site')
