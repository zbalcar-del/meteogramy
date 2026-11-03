"""Vrstevnice pohoří pro karty na webu: python3 meteogram/build_relief.py

Pro každé pohoří vykreslí detail terénu kolem jeho nejvyšší stanice (výřez 9 × 6,75 km) se štítkem
názvu a výšky do web/assets/pohori/<pohoří>.svg, každé pohoří ve vlastní barvě.
Výšky: Copernicus DEM GLO-90 (© DLR e.V. 2010–2014 a © Airbus Defence and Space GmbH 2014–2018,
poskytnuto programem Copernicus Evropské unie a ESA). Dlaždice se jednou stáhnou do build/dem.
Terén se nemění, takže stačí spustit jednou (nebo po přidání stanic).
"""

from html import escape
import json
import math
from pathlib import Path
import urllib.request

import numpy as np
from PIL import Image

from build_pages import groups, slug

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'web' / 'assets' / 'pohori'
DEM = ROOT / 'build' / 'dem'
DEM_URL = 'https://copernicus-dem-90m.s3.amazonaws.com/{0}/{0}.tif'
W, H = 400, 300            # karta má poměr 4 : 3
SPAN_KM = 9                # šířka výřezu kolem vrcholu
LINES = 16                 # nejvýš tolik vrstevnic ve výřezu
# barvy ve stylu systémových barev Apple; sousední karty se liší
COLORS = ['#007aff', '#34c759', '#ff9500', '#af52de', '#30b0c7', '#ff2d55', '#5856d6', '#a2845e',
          '#00c7be', '#ff6b35', '#32ade6', '#d4a017']


class Dem:
    """Mozaika dlaždic 1° × 1°; výška v bodě se čte bilineárně."""

    def __init__(self):
        self.tiles = {}

    def tile(self, la, lo):
        if (la, lo) not in self.tiles:
            name = f'Copernicus_DSM_COG_30_N{la:02d}_00_E{lo:03d}_00_DEM'
            path = DEM / f'{name}.tif'
            if not path.exists():
                DEM.mkdir(parents=True, exist_ok=True)
                path.write_bytes(urllib.request.urlopen(DEM_URL.format(name), timeout=120).read())
            self.tiles[la, lo] = np.asarray(Image.open(path), dtype=np.float64)
        return self.tiles[la, lo]

    def sample(self, lats, lons):
        """Výšky pro mřížku zeměpisných šířek (řádky) a délek (sloupce)."""
        out = np.empty((len(lats), len(lons)))
        for la in np.unique(np.floor(lats).astype(int)):
            for lo in np.unique(np.floor(lons).astype(int)):
                rs = np.nonzero(np.floor(lats).astype(int) == la)[0]
                cs = np.nonzero(np.floor(lons).astype(int) == lo)[0]
                t = self.tile(int(la), int(lo))
                h, w = t.shape
                r = np.clip((la + 1 - lats[rs]) * h, 0, h - 1.001)
                c = np.clip((lons[cs] - lo) * w, 0, w - 1.001)
                r0, c0 = r.astype(int), c.astype(int)
                fr, fc = (r - r0)[:, None], (c - c0)[None, :]
                a, b = t[np.ix_(r0, c0)], t[np.ix_(r0, c0 + 1)]
                cc, d = t[np.ix_(r0 + 1, c0)], t[np.ix_(r0 + 1, c0 + 1)]
                out[np.ix_(rs, cs)] = (a * (1 - fc) + b * fc) * (1 - fr) + (cc * (1 - fc) + d * fc) * fr
        return out


def window(lat, lon, w_km, h_km, nx, ny):
    kx, ky = 111.32 * math.cos(math.radians(lat)), 111.32
    lats = np.linspace(lat + h_km / 2 / ky, lat - h_km / 2 / ky, ny)
    lons = np.linspace(lon - w_km / 2 / kx, lon + w_km / 2 / kx, nx)
    return lats, lons


def blur(z, radius):
    """Jemné vyhlazení (opakovaný průměr okolí), aby vrstevnice nebyly zubaté."""
    for _ in range(radius):
        p = np.pad(z, 1, mode='edge')
        z = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] + 4 * p[1:-1, 1:-1]) / 8
    return z


def contours(z, level):
    """Marching squares: vrátí lomené čáry [(x, y), ...] v souřadnicích mřížky (x = sloupec, y = řádek)."""
    above = z >= level
    a, b, c, d = above[:-1, :-1], above[:-1, 1:], above[1:, 1:], above[1:, :-1]
    case = a * 8 + b * 4 + c * 2 + d
    rows, cols = np.nonzero((case > 0) & (case < 15))

    def cut(r, c, edge):
        # bod na hraně buňky; hrany: 0 nahoře, 1 vpravo, 2 dole, 3 vlevo
        if edge == 0:
            v0, v1 = z[r, c], z[r, c + 1]; t = (level - v0) / (v1 - v0); return (c + t, r), ('h', r, c)
        if edge == 2:
            v0, v1 = z[r + 1, c], z[r + 1, c + 1]; t = (level - v0) / (v1 - v0); return (c + t, r + 1), ('h', r + 1, c)
        if edge == 3:
            v0, v1 = z[r, c], z[r + 1, c]; t = (level - v0) / (v1 - v0); return (c, r + t), ('v', r, c)
        v0, v1 = z[r, c + 1], z[r + 1, c + 1]; t = (level - v0) / (v1 - v0); return (c + 1, r + t), ('v', r, c + 1)

    table = {1: [(3, 2)], 2: [(2, 1)], 3: [(3, 1)], 4: [(0, 1)], 6: [(0, 2)], 7: [(3, 0)], 8: [(3, 0)],
             9: [(0, 2)], 11: [(0, 1)], 12: [(3, 1)], 13: [(2, 1)], 14: [(3, 2)]}
    links, points = {}, {}
    for r, c in zip(rows.tolist(), cols.tolist()):
        k = int(case[r, c])
        if k in (5, 10):  # sedlo: rozhodne průměr rohů
            mid = (z[r, c] + z[r, c + 1] + z[r + 1, c] + z[r + 1, c + 1]) / 4 >= level
            pairs = [(3, 0), (2, 1)] if (k == 5) == mid else [(3, 2), (0, 1)]
        else:
            pairs = table[k]
        for e1, e2 in pairs:
            p1, k1 = cut(r, c, e1)
            p2, k2 = cut(r, c, e2)
            points[k1], points[k2] = p1, p2
            links.setdefault(k1, []).append(k2)
            links.setdefault(k2, []).append(k1)
    lines, seen = [], set()
    # nejdřív otevřené čáry (začínají na okraji), pak uzavřené smyčky
    starts = [k for k, v in links.items() if len(v) == 1] + list(links)
    for start in starts:
        if start in seen:
            continue
        line, prev, cur = [points[start]], None, start
        seen.add(cur)
        while True:
            nxt = [n for n in links[cur] if n != prev and n not in seen]
            if not nxt:
                if len(line) > 2 and start in links[cur] and prev is not None:
                    line.append(points[start])
                break
            prev, cur = cur, nxt[0]
            seen.add(cur)
            line.append(points[cur])
        if len(line) > 3:
            lines.append(line)
    return lines


def simplify(line, tol):
    """Vynechá body, které jsou k předchozímu blíž než tol (v jednotkách obrázku)."""
    out = [line[0]]
    for p in line[1:-1]:
        if math.dist(p, out[-1]) >= tol:
            out.append(p)
    out.append(line[-1])
    return out


def path(line):
    d = f'M{line[0][0]:.1f} {line[0][1]:.1f}'
    return d + ''.join(f'L{x:.1f} {y:.1f}' for x, y in line[1:])


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(x + (y - x) * t):02x}' for x, y in zip(a, b))


def text_width(text, size, bold):
    """Šířka textu v systémovém písmu (podle SF Pro, které štítek používá na Applu)."""
    from PIL import ImageFont
    try:
        font = ImageFont.truetype('/System/Library/Fonts/SFNS.ttf', size)
        if bold:
            font.set_variation_by_axes([600])
    except OSError:
        return len(text) * size * 0.58
    return font.getlength(text)


def render(name, stations, dem, color):
    top = stations[0]  # groups() řadí stanice od nejvyšší
    plat, plon = top['lat'], top['lon']
    span_y = SPAN_KM * H / W
    lats, lons = window(plat, plon, SPAN_KM, span_y, W, H)
    z = blur(dem.sample(lats, lons), 14)  # model zachycuje i les a stavby, vyhlazení je skryje
    lo, hi = float(z.min()), float(z.max())
    step = next((s for s in (5, 10, 20, 25, 50, 100) if (hi - lo) / s <= LINES), 100)
    paths = []
    for level in np.arange(math.ceil(lo / step) * step, hi, step):
        t = (level - lo) / (hi - lo)
        stroke = mix('#ffffff', color, 0.28 + 0.62 * t ** 1.2)
        index = round(level) % (step * 5) == 0
        d = ''.join(path(simplify(line, 2)) for line in contours(z, level) if len(line) > 30)
        if d:
            paths.append(f'<path d="{d}" stroke="{stroke}" stroke-width="{(1.5 if index else 0.85) + 0.5 * t:.2f}"/>')
    kx = 111.32 * math.cos(math.radians(plat))
    px = lambda la, lo: ((lo - lons[0]) * kx / SPAN_KM * W, (lats[0] - la) * 111.32 / span_y * H)
    dots = ''
    for p in stations[1:]:
        x, y = px(p['lat'], p['lon'])
        if 6 < x < W - 6 and 6 < y < H - 6:
            dots += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="#fff" stroke="{color}" stroke-width="2.6" opacity=".9"/>'
    # nejvyšší stanice: bod se světelným kruhem a nad ním bílý štítek jako v mapách
    x, y = px(plat, plon)
    label = escape(top['name'])
    alt = f"{top['alt']} m"
    tw = round((text_width(top['name'], 20, True) + text_width(alt, 20, True)) * 0.85 + 9 + 40)
    lx, ly = x - tw / 2, y - 74
    peak = (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="27" fill="{color}" opacity=".16"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12.5" fill="{color}" stroke="#fff" stroke-width="4.5"/>'
            f'<g filter="url(#sh)"><rect x="{lx:.1f}" y="{ly:.1f}" width="{tw}" height="44" rx="22" fill="#fff"/>'
            f'<path d="M{x - 9:.1f} {ly + 43:.1f}l9 10 9-10z" fill="#fff"/></g>'
            f'<text x="{lx + 20:.1f}" y="{ly + 29:.1f}" font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif" font-size="20">'
            f'<tspan font-weight="600" fill="#1d1d1f">{label}</tspan><tspan dx="9" fill="{color}" font-weight="600">{alt}</tspan></text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            f'<title>{escape(name)} – vrstevnice v okolí stanice {label}</title>'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{mix("#ffffff", color, 0.05)}"/>'
            f'<stop offset="1" stop-color="{mix("#ffffff", color, 0.13)}"/></linearGradient>'
            '<filter id="sh" x="-20%" y="-40%" width="140%" height="200%"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#000" flood-opacity=".14"/></filter></defs>'
            f'<rect width="{W}" height="{H}" fill="url(#g)"/>'
            f'<g fill="none" stroke-linecap="round" stroke-linejoin="round">{"".join(paths)}</g>{dots}{peak}</svg>\n')


def main():
    dem = Dem()
    places = json.loads((ROOT / 'meteogram' / 'places.json').read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    for k, (name, stations) in enumerate(groups(places)):
        svg = render(name, stations, dem, COLORS[k % len(COLORS)])
        (OUT / f'{slug(name)}.svg').write_text(svg)
        print(f'{name}: {len(svg) // 1024} kB', flush=True)


if __name__ == '__main__':
    main()
