"""Sestaví seznam lyžařských středisek do meteogram/resorts.json.

Názvy a přibližné polohy bere z mapy „Meteogramy Aladin – lyžařská střediska“ na chmi.cz. Předpověď
patří na vrchol areálu: z OpenStreetMap vezme vleky a lanovky, které na sebe u bodu ČHMÚ navazují,
a místo posune na nejvýš položenou horní stanici (výška z modelu terénu EU-DEM 25 m). Kde vleky
nejsou (běžkařská místa), zůstane bod a výška ČHMÚ. Předpověď pak počítá fetch.py stejně jako pro stanice.
Středisko, které leží přímo u horské stanice (do SAME_KM), nemá vlastní předpověď ani stránku: v seznamu
středisek odkazuje na stanici (pole "same"), protože ta má navíc měření.
Spouští se ručně, když chceme seznam obnovit:  .venv/bin/python meteogram/build_resorts.py
"""

import json
import math
import subprocess
import time
import urllib.parse
from pathlib import Path

from build_places import RANGES, in_range, km, slug

HERE = Path(__file__).resolve().parent
OUT = HERE / "resorts.json"
LIST = "https://data-provider.chmi.cz/api/poi/data/map/lyze/4"
GRAPH = "https://data-provider.chmi.cz/api/graphs/graf.meteogram/{}"
KINDS = {"lyzarskaStrediska", "lyzarskeZavody"}  # bez souhrnných bodů za celá pohoří
SAME_KM = 0.6     # vrchol areálu tak blízko stanice = stejné místo
SIMILAR_KM = 1.0  # ... nebo kousek od stanice v téměř stejné výšce (do SIMILAR_M)
SIMILAR_M = 60
MIN_LIFT_KM = 0.2  # kratší vleky (dětské, lana) ani lanové dráhy pro motokáry vrchol areálu neurčují
OVERPASS = "https://overpass-api.de/api/interpreter"
LIFTS = ('[out:json][timeout:120];way["aerialway"~"^(chair_lift|gondola|cable_car|mixed_lift|drag_lift|t-bar|j-bar|'
         'platter|rope_tow)$"](48.5,12.0,51.1,18.9);out geom tags;')
LIFTS_CZ = ('[out:json][timeout:120];area["ISO3166-1"="CZ"][admin_level=2]->.cz;way["aerialway"~"^(chair_lift|gondola|'
            'cable_car|mixed_lift|drag_lift|t-bar|j-bar|platter|rope_tow)$"](area.cz);out ids;')
ELEVATION = "https://api.opentopodata.org/v1/eudem25m?locations={}"
SEED_KM = 0.8   # vleky tak blízko bodu ČHMÚ patří ke středisku
NEAR_KM = 1.5   # když u bodu žádný není, vezme se nejbližší vlek do této vzdálenosti
RANGE_KM = 12   # středisko mimo hranice pohoří patří k pohoří nejbližší stanice, když je takhle blízko
LINK_KM = 0.35  # další vlek patří do areálu, když na některý z nich navazuje
AREA_KM = 3.5   # areál nesahá dál od bodu ČHMÚ (aby se nespojil se sousedním)


def get(url, data=None):
    # curl místo urllib: Python z Command Line Tools neumí TLS některých serverů (Overpass)
    cmd = ["curl", "-sSf", "--max-time", "180", "-A", "Meteogramy.cz (seznam stredisek)", url]
    if data:
        cmd += ["--data-binary", "@-"]
    for attempt in range(4):
        run = subprocess.run(cmd, input=data, capture_output=True)
        if run.returncode == 0:
            return json.loads(run.stdout)
        time.sleep(20 * (attempt + 1))
    raise SystemExit(f"nepodařilo se stáhnout {url}: {run.stderr.decode().strip()}")


def length(lift):
    g = lift["geometry"]
    return km(g[0]["lat"], g[0]["lon"], g[-1]["lat"], g[-1]["lon"])


def lift_area(lat, lon, lifts, czech):
    """Vleky areálu: ty u bodu a všechny, které na ně navazují, nejdál AREA_KM od bodu."""
    def close(lift, la, lo, d):
        return any(km(la, lo, g["lat"], g["lon"]) < d for g in lift["geometry"])
    pool = [l for l in lifts if close(l, lat, lon, AREA_KM) and l["tags"]["aerialway"] != "rope_tow" and length(l) >= MIN_LIFT_KM]
    # za hranici jen u středisek, která leží celá v cizině (Hochficht); jinak se nesmí chytit sousední areál
    if any(l["id"] in czech for l in pool):
        pool = [l for l in pool if l["id"] in czech]
    area = [l for l in pool if close(l, lat, lon, SEED_KM)]
    if not area and pool:
        dist = lambda l: min(km(lat, lon, g["lat"], g["lon"]) for g in l["geometry"])
        nearest = min(pool, key=dist)
        area = [nearest] if dist(nearest) < NEAR_KM else []
    grown = True
    while grown:
        grown = False
        for l in pool:
            if l in area:
                continue
            if any(close(l, g["lat"], g["lon"], LINK_KM) for a in area for g in (a["geometry"][0], a["geometry"][-1])):
                area.append(l)
                grown = True
    return area


def elevations(points):
    out = {}
    points = sorted(points)
    for i in range(0, len(points), 100):
        chunk = points[i:i + 100]
        data = get(ELEVATION.format("|".join(f"{a},{b}" for a, b in chunk)))
        out.update(zip(chunk, (r["elevation"] for r in data["results"])))
        time.sleep(1.2)
    return out


def utm33_to_wgs(x, y):
    """UTM zóna 33N (EPSG:32633) -> zeměpisná šířka a délka (WGS 84)."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x -= 500000
    mu = y / k0 / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    p = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
         + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    n = a / math.sqrt(1 - e2 * math.sin(p) ** 2)
    t, c = math.tan(p) ** 2, ep2 * math.cos(p) ** 2
    r = a * (1 - e2) / (1 - e2 * math.sin(p) ** 2) ** 1.5
    d = x / (n * k0)
    lat = p - (n * math.tan(p) / r) * (d ** 2 / 2 - (5 + 3 * t + 10 * c - 4 * c ** 2 - 9 * ep2) * d ** 4 / 24
                                       + (61 + 90 * t + 298 * c + 45 * t ** 2 - 252 * ep2 - 3 * c ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t + c) * d ** 3 / 6 + (5 - 2 * c + 28 * t - 3 * c ** 2 + 8 * ep2 + 24 * t ** 2) * d ** 5 / 120) / math.cos(p)
    return math.degrees(lat), 15 + math.degrees(lon)


def main():
    stations = json.loads((HERE / "places.json").read_text())
    lifts = get(OVERPASS, urllib.parse.urlencode({"data": LIFTS}).encode())["elements"]
    czech = {e["id"] for e in get(OVERPASS, urllib.parse.urlencode({"data": LIFTS_CZ}).encode())["elements"]}
    points = []
    for f in get(LIST)["features"]:
        pr = f["properties"]
        if pr["poiType"] not in KINDS:
            continue
        lat, lon = utm33_to_wgs(*f["geometry"]["coordinates"])
        area = lift_area(lat, lon, lifts, czech)
        ends = {(round(g["lat"], 5), round(g["lon"], 5)) for l in area for g in (l["geometry"][0], l["geometry"][-1])}
        points.append((" ".join(pr["name"].replace("\u00a0", " ").split()), lat, lon,
                       int(pr["url"].split("/")[-1].split("-")[0]), ends))
    elev = elevations(set().union(*(p[4] for p in points)))

    # adresy stanic, pohoří a stránek webu jsou obsazené
    taken = {p["id"] for p in stations} | {slug(r[0]) for r in RANGES} | {"krusne-hory", "ostatni", "data", "api", "assets",
             "sprava-webu-meteo", "snehove-zpravodajstvi", "lavinova-predpoved", "zimy-na-horach", "o-projektu", "ochrana-soukromi"}
    resorts, tops, same = [], {}, []
    for name, lat0, lon0, chmi, ends in points:
        lat, lon = lat0, lon0
        if ends:
            (lat, lon), alt, top = max(((e, elev[e]) for e in ends), key=lambda x: x[1]) + (True,)
        else:
            alt, top = get(GRAPH.format(chmi))["z"], False
            time.sleep(0.2)
        st = min(stations, key=lambda s: km(lat, lon, s["lat"], s["lon"]))
        d = km(lat, lon, st["lat"], st["lon"])
        # bod ČHMÚ přímo u stanice, která leží výš než vleky (stanice na vrcholu hory) -> také stanice
        st0 = min(stations, key=lambda s: km(lat0, lon0, s["lat"], s["lon"]))
        summit = km(lat0, lon0, st0["lat"], st0["lon"]) < SAME_KM and st0["alt"] >= alt - 30
        if summit:
            st = st0
        if d < SAME_KM or (d < SIMILAR_KM and abs(alt - st["alt"]) < SIMILAR_M) or summit:
            # vrchol areálu je u stanice: v seznamu středisek odkaz na stanici
            same.append(f"{name} = {st['name']}")
            resorts.append({"id": st["id"], "name": name, "range": st["range"], "alt": st["alt"], "same": st["id"]})
            continue
        key = (round(lat, 4), round(lon, 4))
        if key in tops:  # dva body ČHMÚ ve stejném areálu
            tops[key].setdefault("aka", []).append(name)
            continue
        rng = in_range(lat, lon)
        if rng == "Ostatní" and km(lat, lon, st["lat"], st["lon"]) < RANGE_KM:
            rng = st["range"]
        pid = slug(name)
        if pid in taken:
            pid = f"{pid}-{slug(rng)}" if any(r["id"] == pid and "same" not in r for r in resorts) else f"{pid}-skiareal"
        taken.add(pid)
        tops[key] = r = {"id": pid, "name": name, "range": rng, "lat": round(lat, 5), "lon": round(lon, 5),
                         "alt": round(alt), "kind": "resort", "top": top, "chmi": chmi}
        resorts.append(r)
    resorts.sort(key=lambda p: -p["alt"])
    OUT.write_text(json.dumps(resorts, ensure_ascii=False, indent=1))
    print(f"středisek: {len(resorts)} -> {OUT}")
    print(f"vrchol u stanice: {len(same)}: " + "; ".join(same))
    print("sloučeno: " + "; ".join(f"{r['name']} + {', '.join(r['aka'])}" for r in resorts if r.get("aka")))


if __name__ == "__main__":
    main()
