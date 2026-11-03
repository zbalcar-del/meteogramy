"""Sestaví seznam míst z meteorologických stanic ČHMÚ do meteogram/places.json.

Bere aktuální metadata stanic z otevřených dat ČHMÚ a nechá horské stanice (od MIN_ALT m n. m.).
Spouští se ručně, když chceme seznam obnovit:  .venv/bin/python meteogram/build_places.py
"""

import json
import math
import re
import unicodedata
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "places.json"
META = "https://opendata.chmi.cz/meteorology/climate/now/metadata/meta1-{}.json"
MIN_ALT = 700
DUPLICATE_KM = 0.3

# pohoří: (název, lat_min, lat_max, lon_min, lon_max), první shoda vyhrává
RANGES = [
    ("Králický Sněžník", 50.12, 50.26, 16.74, 16.95),
    ("Krkonoše", 50.60, 50.80, 15.40, 15.98),
    ("Jizerské hory", 50.74, 50.93, 15.00, 15.40),
    ("Ještěd a Kozákov", 50.55, 50.78, 14.88, 15.30),
    ("Lužické hory", 50.74, 50.90, 14.35, 14.85),
    ("Orlické hory", 50.00, 50.45, 16.20, 16.74),
    ("Rychlebské hory", 50.25, 50.42, 16.95, 17.25),
    ("Hrubý Jeseník", 49.95, 50.26, 16.95, 17.45),
    ("Nízký Jeseník", 49.60, 50.00, 17.20, 17.95),
    ("Beskydy", 49.33, 49.72, 18.05, 18.95),
    ("Javorníky", 49.12, 49.33, 17.95, 18.40),
    ("Hostýnské vrchy", 49.25, 49.45, 17.55, 17.95),
    ("Bílé Karpaty", 48.78, 49.15, 17.35, 18.10),
    ("Doupovské hory", 50.18, 50.35, 12.95, 13.30),
    ("České středohoří", 50.40, 50.78, 13.75, 14.45),
    ("Slavkovský les", 49.95, 50.18, 12.50, 12.95),
    ("Český les", 49.35, 49.95, 12.35, 12.85),
    ("Novohradské hory", 48.55, 48.82, 14.55, 14.98),
    ("Šumava", 48.55, 49.35, 12.80, 14.45),
    ("Brdy", 49.55, 49.92, 13.65, 14.15),
    ("Vysočina", 49.10, 49.90, 15.00, 16.45),
]


def in_range(lat, lon):
    # Krušné hory jsou šikmý pás podél hranice, box by zasahoval do Středohoří
    if lon <= 14.0 and lat >= 50.25 and lat > 50.33 + max(0, lon - 12.97) * 0.36:
        return "Krušné hory"
    for name, a, b, c, d in RANGES:
        if a <= lat <= b and c <= lon <= d:
            return name
    return "Ostatní"


def km(lat1, lon1, lat2, lon2):
    return math.hypot((lat2 - lat1) * 111.2, (lon2 - lon1) * 111.2 * math.cos(math.radians((lat1 + lat2) / 2)))


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def load_meta():
    for back in range(5):
        day = (date.today() - timedelta(days=back)).strftime("%Y%m%d")
        try:
            return json.loads(urllib.request.urlopen(META.format(day), timeout=60).read())["data"]["data"]
        except Exception:
            continue
    raise SystemExit("metadata stanic ČHMÚ nejsou dostupná")


def main():
    meta = load_meta()
    cols = meta["header"].split(",")
    rows = [dict(zip(cols, r)) for r in meta["values"]]
    # GEOGR1 = zeměpisná délka, GEOGR2 = šířka; jen ČR (síť ALADIN CZ 1 km) a horské polohy
    rows = [r for r in rows if 12.0 <= r["GEOGR1"] <= 18.99 and 48.5 <= r["GEOGR2"] <= 51.09 and r["ELEVATION"] >= MIN_ALT]
    rows.sort(key=lambda r: r["BEGIN_DATE"])  # při duplicitě vyhrává stanice s delší řadou

    places, ids = [], set()
    for r in rows:
        lat, lon = r["GEOGR2"], r["GEOGR1"]
        if any(km(lat, lon, p["lat"], p["lon"]) < DUPLICATE_KM for p in places):
            continue
        name = re.sub(r"\s+", " ", r["FULL_NAME"]).strip()
        pid = slug(name)
        while pid in ids:
            pid += "-2"
        ids.add(pid)
        places.append({"id": pid, "name": name, "range": in_range(lat, lon), "lat": round(lat, 5), "lon": round(lon, 5),
                       "alt": round(r["ELEVATION"]), "station": r["GH_ID"], "since": int(r["BEGIN_DATE"][:4])})
    places.sort(key=lambda p: -p["alt"])
    OUT.write_text(json.dumps(places, ensure_ascii=False, indent=1))
    print(f"stanic: {len(places)} -> {OUT}")


if __name__ == "__main__":
    main()
