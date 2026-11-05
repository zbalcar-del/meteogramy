"""Stáhne poslední běh modelu ALADIN CZ (výstup v rastru ~1 km) z otevřených dat ČHMÚ
a vytáhne z něj časové řady pro české hory do web/data/*.json."""

import bz2
import json
import math
import re
import shutil
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes as ec
import numpy as np

BASE = "https://opendata.chmi.cz/meteorology/weather/nwp_aladin/CZ_1km"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "data"
KEEP_RUNS = 8
SEARCH_RADIUS_KM = 1.5

# název souboru -> (klíč v JSON, převod, akumulované od startu?)
FIELDS = {
    "CLSTEMPERATURE": ("t", lambda v: v - 273.15, False),
    "CLSHUMI_RELATIVE": ("rh", lambda v: v * 100, False),
    "CLSWIND_SPEED": ("ws", lambda v: v, False),
    "CLSWIND_DIREC": ("wd", lambda v: v, False),
    "CLSRAFAL_MOD_XFU": ("gust", lambda v: v, False),
    "MSLPRESSURE": ("mslp", lambda v: v / 100, False),
    "SURFPREC_TOTAL": ("prec", lambda v: v, True),
    "SURFSNOWFALL": ("snow", lambda v: v, True),
    "SUNSHINE_DUR": ("sun", lambda v: v / 60, True),
    "SURFNEBUL_TOTALE": ("clt", lambda v: v * 100, False),
    "SURFNEBUL_BASSE": ("cll", lambda v: v * 100, False),
    "SURFNEBUL_MOYENN": ("clm", lambda v: v * 100, False),
    "SURFNEBUL_HAUTE": ("clh", lambda v: v * 100, False),
}
OROGRAPHY = "SURFGEOPOTENTIEL"

# místa = horské stanice ČHMÚ (build_places.py) a vrcholy lyžařských středisek (build_resorts.py);
# středisko s vrcholem u stanice nemá vlastní předpověď, v seznamu středisek jen odkazuje na stanici
_HERE = Path(__file__).resolve().parent
_RESORTS = json.loads((_HERE / "resorts.json").read_text()) if (_HERE / "resorts.json").exists() else []
PLACES = json.loads((_HERE / "places.json").read_text()) + [r for r in _RESORTS if "same" not in r]
LINKS = [{k: r[k] for k in ("name", "range", "alt", "same")} for r in _RESORTS if "same" in r]
# uzel modelu: v okolí stanice ten s výškou nejbližší skutečné výšce (1 km vzdálenosti "stojí" 60 m výšky)
DIST_PENALTY_M_PER_KM = 60
# údolí pro poznání inverze: nejnižší bod modelu do VALLEY_RADIUS_KM, aspoň VALLEY_MIN_DROP_M pod uzlem stanice
VALLEY_RADIUS_KM = 8
VALLEY_MIN_DROP_M = 300
# z údolního bodu stačí teplota a nízká oblačnost (mlha)
VALLEY_FIELDS = {"t": "vt", "cll": "vcll"}

FILE_RE = re.compile(r"ALADCZ1K4opendata_(\d{10})_([A-Z0-9_]+)\.grb\.bz2")


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def list_runs():
    """Vrátí {run: {pole: url}} pro všechny běhy na serveru."""
    runs = {}
    for cycle in ("00", "06", "12", "18"):
        url = f"{BASE}/{cycle}/"
        html = urllib.request.urlopen(url, timeout=30).read().decode()
        for run, field in FILE_RE.findall(html):
            runs.setdefault(run, {})[field] = f"{url}ALADCZ1K4opendata_{run}_{field}.grb.bz2"
    return runs


def download(url, dest):
    with urllib.request.urlopen(url, timeout=300) as r, open(dest, "wb") as f:
        dec = bz2.BZ2Decompressor()
        while chunk := r.read(1 << 20):
            f.write(dec.decompress(chunk))


def read_messages(path):
    """Vrátí seznam (step, values, grid) pro všechny zprávy v souboru."""
    out = []
    with open(path, "rb") as f:
        while (g := ec.codes_grib_new_from_file(f)) is not None:
            try:
                grid = {k: ec.codes_get(g, k) for k in (
                    "Ni", "Nj",
                    "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
                    "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees")}
                out.append((int(ec.codes_get(g, "endStep")), ec.codes_get_values(g), grid))
            finally:
                ec.codes_release(g)
    return out


def pick_nodes(oro, grid):
    """Pro každé místo najde uzel modelu do SEARCH_RADIUS_KM, jehož výška nejlépe odpovídá skutečné."""
    ni, nj = grid["Ni"], grid["Nj"]
    lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
    dlat, dlon = grid["jDirectionIncrementInDegrees"], grid["iDirectionIncrementInDegrees"]
    z = oro.reshape(nj, ni) / 9.80665
    nodes = {}
    for pl in PLACES:
        lat, lon = pl["lat"], pl["lon"]
        j0, i0 = round((lat - lat0) / dlat), round((lon - lon0) / dlon)
        best = None
        for dj in range(-3, 4):
            for di in range(-3, 4):
                j, i = j0 + dj, i0 + di
                if not (0 <= j < nj and 0 <= i < ni):
                    continue
                nlat, nlon = lat0 + j * dlat, lon0 + i * dlon
                dist = math.hypot((nlat - lat) * 111.2, (nlon - lon) * 111.2 * math.cos(math.radians(lat)))
                if dist > SEARCH_RADIUS_KM:
                    continue
                cost = abs(z[j, i] - pl["alt"]) + DIST_PENALTY_M_PER_KM * dist
                if best is None or cost < best["cost"]:
                    best = {"idx": j * ni + i, "lat": nlat, "lon": nlon,
                            "alt": float(z[j, i]), "dist": dist, "cost": cost}
        nodes[pl["id"]] = best
    return nodes


def pick_valleys(oro, grid, nodes):
    """Pro každé místo najde nejnižší bod modelu v okolí (údolí pod stanicí); None, když není výrazně níž."""
    ni, nj = grid["Ni"], grid["Nj"]
    lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
    dlat, dlon = grid["jDirectionIncrementInDegrees"], grid["iDirectionIncrementInDegrees"]
    z = oro.reshape(nj, ni) / 9.80665
    valleys = {}
    for pl in PLACES:
        lat, lon, node = pl["lat"], pl["lon"], nodes[pl["id"]]
        j0, i0 = round((lat - lat0) / dlat), round((lon - lon0) / dlon)
        rj = math.ceil(VALLEY_RADIUS_KM / (dlat * 111.2))
        ri = math.ceil(VALLEY_RADIUS_KM / (dlon * 111.2 * math.cos(math.radians(lat))))
        best = None
        for j in range(max(0, j0 - rj), min(nj, j0 + rj + 1)):
            for i in range(max(0, i0 - ri), min(ni, i0 + ri + 1)):
                nlat, nlon = lat0 + j * dlat, lon0 + i * dlon
                dist = math.hypot((nlat - lat) * 111.2, (nlon - lon) * 111.2 * math.cos(math.radians(lat)))
                if dist <= VALLEY_RADIUS_KM and (best is None or z[j, i] < best["alt"]):
                    best = {"idx": j * ni + i, "lat": nlat, "lon": nlon, "alt": float(z[j, i]), "dist": dist}
        valleys[pl["id"]] = best if best and node["alt"] - best["alt"] >= VALLEY_MIN_DROP_M else None
    return valleys


def iso(d):
    return d.isoformat().replace("+00:00", "Z")


def process_run(run, urls, dest):
    """Stáhne běh a zapíše dest/index.json (seznam míst) a dest/<id>.json (časové řady)."""
    work = Path(tempfile.mkdtemp(prefix=f"aladin_{run}_"))
    try:
        needed = list(FIELDS) + [OROGRAPHY]
        log(f"stahuji běh {run} ({len(needed)} souborů)")
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda f: download(urls[f], work / f"{f}.grb"), needed))

        oro_msgs = read_messages(work / f"{OROGRAPHY}.grb")
        nodes = pick_nodes(oro_msgs[0][1], oro_msgs[0][2])
        valleys = pick_valleys(oro_msgs[0][1], oro_msgs[0][2], nodes)

        series = {pid: {} for pid in nodes}
        steps_all = None
        for fname, (key, conv, accum) in FIELDS.items():
            msgs = read_messages(work / f"{fname}.grb")
            (work / f"{fname}.grb").unlink()
            by_step = {s: v for s, v, _ in msgs}
            steps = sorted(s for s in by_step if s >= 1)
            steps_all = steps if steps_all is None else [s for s in steps_all if s in by_step]
            for pid, node in nodes.items():
                vals = {s: float(conv(by_step[s][node["idx"]])) for s in by_step}
                if accum:
                    # akumulace od startu -> přírůstek za hodinu
                    vals = {s: max(0.0, vals[s] - vals.get(s - 1, 0.0)) for s in steps}
                series[pid][key] = vals
                valley = valleys[pid]
                if key in VALLEY_FIELDS and valley:
                    series[pid][VALLEY_FIELDS[key]] = {s: float(conv(by_step[s][valley["idx"]])) for s in by_step}

        start = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        times = [iso(start + timedelta(hours=s)) for s in steps_all]
        dest.mkdir(parents=True, exist_ok=True)
        index = {"model": "ALADIN CZ", "source": "ČHMÚ – otevřená data", "run": iso(start), "places": [], "links": LINKS}
        for pl in PLACES:
            node = nodes[pl["id"]]
            meta = {**pl, "node": {"lat": round(node["lat"], 4), "lon": round(node["lon"], 4),
                                   "alt": round(node["alt"]), "dist": round(node["dist"], 2)}}
            valley = valleys[pl["id"]]
            if valley:
                meta["valley"] = {"lat": round(valley["lat"], 4), "lon": round(valley["lon"], 4),
                                  "alt": round(valley["alt"]), "dist": round(valley["dist"], 1)}
            index["places"].append(meta)
            entry = {"id": pl["id"], "times": times}
            for key, vals in series[pl["id"]].items():
                entry[key] = [round(vals[s], 2) for s in steps_all]
            (dest / f"{pl['id']}.json").write_text(json.dumps(entry, separators=(",", ":")))
        (dest / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def write_summary(dest):
    """Souhrn všech míst v jednom souboru pro úvodní stránku (aby nemusela stahovat každé místo zvlášť)."""
    index = json.loads((dest / "index.json").read_text())
    out = {"times": None, "places": {}}
    for pl in index["places"]:
        e = json.loads((dest / f"{pl['id']}.json").read_text())
        out["times"] = e["times"]
        out["places"][pl["id"]] = {k: [round(v, d) for v in e[k]]
                                   for k, d in (("t", 1), ("gust", 1), ("prec", 2), ("snow", 2), ("sun", 0))}
    (dest / "summary.json").write_text(json.dumps(out, separators=(",", ":")))


def update_index():
    runs = sorted((p.name for p in OUT.glob("20*") if (p / "index.json").exists()), reverse=True)
    for old in runs[KEEP_RUNS:]:
        shutil.rmtree(OUT / old)
    for run in runs[:KEEP_RUNS]:
        if not (OUT / run / "summary.json").exists():
            write_summary(OUT / run)
    for stale in OUT.glob("20*.json"):  # starý formát (jeden soubor na běh)
        stale.unlink()
    (OUT / "index.json").write_text(json.dumps({"runs": runs[:KEEP_RUNS]}))


def main(force=False):
    OUT.mkdir(parents=True, exist_ok=True)
    runs = list_runs()
    needed = set(FIELDS) | {OROGRAPHY}
    complete = sorted((r for r, f in runs.items() if needed <= f.keys()), reverse=True)
    if not complete:
        log("na serveru není žádný úplný běh")
        return False
    # nejnovější běh může být ještě rozpracovaný na serveru -> případně zkusit starší
    for run in complete[:3]:
        target = OUT / run
        if (target / "index.json").exists() and not force:
            log(f"běh {run} už je stažený")
            update_index()
            return False
        tmp = OUT / f".{run}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            process_run(run, runs[run], tmp)
        except Exception as e:
            log(f"běh {run} se nepodařilo zpracovat: {e}")
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        shutil.rmtree(target, ignore_errors=True)
        tmp.rename(target)
        update_index()
        log(f"hotovo: {run} ({len(PLACES)} míst)")
        return True
    return False


if __name__ == "__main__":
    main(force="--force" in sys.argv)
