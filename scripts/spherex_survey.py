"""SPHEREx DR1+QR3 spectral survey of map objects -> frontend/public/galaxy/spherex_survey.json.

Picks a set of nearby objects from the built map catalogue (``data/galaxy/catalog.npz``):
the challenge targets, brown dwarfs and planet-hosting M dwarfs within 25 pc, white dwarfs, and
a sample of *uncharted* objects (no Gaia colour, no catalogue type). For each one it measures the
spectrum from every SPHEREx image (``backend.galaxy.spectral.measure_spectrum``) and classifies it.
Results are written after every object, so the survey can be stopped and resumed.

Then re-run ``python -m scripts.build_galaxy_map`` to fold the SPHEREx classes into the map.

    python -m scripts.spherex_survey [--limit 40] [--workers 2]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from backend.galaxy.catalog import load_catalog
from backend.galaxy.classes import CLASS_ID, FLAGS
from backend.galaxy.spectral import GAIA_REF_MJD, measure_spectrum

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "public" / "galaxy" / "spherex_survey.json"
LIVE = ROOT / "cache" / "galaxy" / "spectra"
NAMED = ["Barnard's star", "eps Ind B", "WISE J085510.83-071442.5", "RW Aur", "R Dra", "Teegarden's Star",
         "Wolf 359", "Luhman 16", "GJ 1214", "LHS 1140", "Ross 128", "GJ 1061", "YZ Cet", "LP 791-18", "Gliese 229B",
         "Gliese 570 D", "van Maanen's Star", "UGPS J072227.51-054031.2", "WISE J104915.57-531906.1", "LHS 3844", "GJ 3512"]


def pick(c: dict, limit: int) -> list[int]:
    name = np.char.lower(c["name"].astype(str))
    dist, cls, flags, src, gmag = c["dist"], c["cls"], c["flags"], c["src"], c["gmag"]
    chosen: list[int] = []

    def add(i):
        if i not in chosen and len(chosen) < limit:
            chosen.append(int(i))

    for n in NAMED:
        hits = np.where(name == n.lower())[0]
        if not len(hits):
            hits = np.where(np.char.startswith(name, n.lower()))[0]
        if len(hits):
            add(hits[np.argmin(dist[hits])])
    not_bright = ~(gmag < 9.5)  # brighter stars saturate SPHEREx
    for sel, n in (((cls == CLASS_ID["bd"]) & (dist < 20), 4),
                   (((flags & FLAGS["planet_host"]) > 0) & (cls == CLASS_ID["m"]) & (dist < 25) & not_bright, 2),
                   ((cls == CLASS_ID["wd"]) & (dist < 15) & not_bright, 1),
                   ((src == "none") & (dist < 30), 4),
                   ((src == "model") & ((flags & FLAGS["low_confidence"]) > 0) & (dist < 25) & not_bright, 2)):
        for i in np.where(sel)[0][np.argsort(dist[sel])][:n]:
            add(i)
    return chosen


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args(argv)
    c = load_catalog()
    targets = pick(c, a.limit)
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"releases": ["DR1", "QR3"], "objects": []}
    done["objects"] = [o for o in done["objects"] if o.get("status") != "error"]  # retry earlier failures
    have = {o["i"] for o in done["objects"]}
    todo = [i for i in targets if i not in have]
    print(f"{len(targets)} targets, {len(todo)} to measure", flush=True)
    LIVE.mkdir(parents=True, exist_ok=True)

    def run(i: int) -> dict:
        sid = int(c["source_id"][i])
        nm = str(c["name"][i]) or (f"Gaia DR3 {sid}" if sid > 0 else f"object {i}")
        pmra, pmdec = float(c["pmra"][i]), float(c["pmdec"][i])
        r = measure_spectrum(float(c["ra"][i]), float(c["dec"][i]), pmra=pmra if math.isfinite(pmra) else None,
                             pmdec=pmdec if math.isfinite(pmdec) else None,
                             ref_mjd=GAIA_REF_MJD if sid > 0 else 51544.5, name=nm)
        r["i"] = i
        return r

    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(run, i): i for i in todo}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001
                r = {"i": i, "name": str(c["name"][i]), "status": "error", "error": str(exc)[:300], "ra": float(c["ra"][i]),
                     "dec": float(c["dec"][i]), "spectrum": [], "classification": None}
            done["objects"].append(r)
            done["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            OUT.write_text(json.dumps(done, separators=(",", ":")), encoding="utf-8")
            (LIVE / f"{i}.json").write_text(json.dumps(r), encoding="utf-8")
            cl = r.get("classification") or {}
            print(f"[{len(done['objects'])}] {r.get('name')}: {r.get('status')} {cl.get('label', '')} "
                  f"{cl.get('confidence', '')} frames={r.get('n_frames')} t={r.get('runtime_s')}s", flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(sys.argv[1:])
