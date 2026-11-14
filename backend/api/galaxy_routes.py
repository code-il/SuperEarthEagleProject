"""3D galaxy map routes: per-object details and on-demand SPHEREx DR1+QR3 spectra.

The render buffer and named-object lists are static files (``frontend/public/galaxy``,
built by ``scripts/build_galaxy_map.py``). These routes add what does not fit there:

* ``GET /api/galaxy/object/{i}``   full catalogue record of map object ``i`` (+ its planets
                                    and SPHEREx spectrum when it has them);
* ``GET /api/galaxy/spectrum/{i}`` measure (or return the cached) SPHEREx spectrum of object
                                    ``i`` and classify it; 1–3 min the first time (heavy job);
* ``GET /api/galaxy/survey``       every object with a SPHEREx spectrum so far (survey file +
                                    spectra measured live), for the map's "charted" layer.
"""
from __future__ import annotations

import json
import math
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException

from backend.data_acquisition.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend" / "public" / "galaxy"
router = APIRouter(prefix="/api/galaxy", tags=["galaxy map"])
_lock = threading.Lock()
_object_locks: dict[int, threading.Lock] = {}


def _catalog_path() -> Path:
    return ROOT / "cache" / "galaxy" / "catalog.npz"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, np.ndarray]:
    p = _catalog_path()
    if not p.exists():
        raise HTTPException(503, "The galaxy map catalogue is not built yet: run python -m scripts.build_galaxy_map")
    with np.load(p, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


@lru_cache(maxsize=1)
def _systems() -> dict[int, dict[str, Any]]:
    f = PUBLIC / "systems.json"
    return {s["i"]: s for s in json.loads(f.read_text(encoding="utf-8"))} if f.exists() else {}


def _spectra_dir() -> Path:
    d = Path(get_settings().cache_dir) / "galaxy" / "spectra"
    d.mkdir(parents=True, exist_ok=True)
    return d


@lru_cache(maxsize=1)
def _survey_static() -> dict[int, dict[str, Any]]:
    f = PUBLIC / "spherex_survey.json"
    if not f.exists():
        return {}
    return {o["i"]: o for o in json.loads(f.read_text(encoding="utf-8")).get("objects", []) if "i" in o}


def _live(i: int) -> dict[str, Any] | None:
    f = _spectra_dir() / f"{i}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def object_record(i: int) -> dict[str, Any]:
    c = _catalog()
    n = len(c["ra"])
    if not 0 <= i < n:
        raise HTTPException(404, f"No map object {i}")
    sid = int(c["source_id"][i])
    rec = {
        "i": i, "name": str(c["name"][i]) or None,
        "gaia_dr3_id": str(sid) if sid > 0 else None, "simbad_oid": -sid if sid < 0 else None,
        "ra": _num(c["ra"][i]), "dec": _num(c["dec"][i]), "parallax_mas": _num(c["plx"][i]),
        "distance_pc": _num(c["dist"][i]), "pmra": _num(c["pmra"][i]), "pmdec": _num(c["pmdec"][i]),
        "radial_velocity": _num(c["rv"][i]), "v_tan_km_s": _num(c["vtan"][i]),
        "gmag": _num(c["gmag"][i]), "bp_rp": _num(c["bp_rp"][i]), "teff_gspphot": _num(c["teff"][i]),
        "ruwe": _num(c["ruwe"][i]), "otype": str(c["otype"][i]) or None, "sp_type": str(c["sptype"][i]) or None,
        "class_id": int(c["cls"][i]), "class_source": str(c["src"][i]), "confidence": _num(c["prob"][i]),
        "flags": int(c["flags"][i]), "xyz_pc": [float(v) for v in c["xyz"][i]],
    }
    if rec["distance_pc"] and rec["gmag"] is not None:
        rec["abs_g"] = rec["gmag"] - 5 * math.log10(rec["distance_pc"] / 10)
    rec["system"] = _systems().get(i)
    rec["spherex"] = _live(i) or _survey_static().get(i)
    return rec


@router.get("/object/{i}")
def galaxy_object(i: int):
    """Full record of one map object (Gaia DR3 / SIMBAD / Exoplanet Archive / SPHEREx)."""
    return object_record(i)


@router.get("/spectrum/{i}")
def galaxy_spectrum(i: int, refresh: bool = False):
    """SPHEREx DR1+QR3 spectrum of map object ``i``, measured on first request and cached."""
    with _lock:
        olock = _object_locks.setdefault(i, threading.Lock())
    with olock:  # a second request for the same object waits for the first and gets its result
        if not refresh:
            hit = _live(i) or _survey_static().get(i)
            if hit:
                return hit
        return _measure(i)


def _measure(i: int) -> dict[str, Any]:
    from backend.galaxy.spectral import GAIA_REF_MJD, measure_spectrum

    rec = object_record(i)
    if rec["ra"] is None or rec["dec"] is None:
        raise HTTPException(400, "Object has no position")
    # SIMBAD positions are J2000; Gaia positions are J2016.0.
    ref = GAIA_REF_MJD if rec["gaia_dr3_id"] else 51544.5
    try:
        out = measure_spectrum(rec["ra"], rec["dec"], pmra=rec["pmra"], pmdec=rec["pmdec"], ref_mjd=ref,
                               name=rec["name"] or (f"Gaia DR3 {rec['gaia_dr3_id']}" if rec["gaia_dr3_id"] else None))
    except Exception as exc:  # noqa: BLE001
        from backend.api.data_routes import _http_error
        raise _http_error(exc)
    out["i"] = i
    with _lock:
        (_spectra_dir() / f"{i}.json").write_text(json.dumps(out), encoding="utf-8")
    return out


@router.get("/survey")
def galaxy_survey():
    """Objects with a SPHEREx spectrum: id, status, class (for the map's charted layer)."""
    rows = dict(_survey_static())
    for f in _spectra_dir().glob("*.json"):
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
            rows[int(f.stem)] = o
        except (ValueError, OSError):
            continue
    return {"objects": [{"i": i, "name": o.get("name"), "status": o.get("status"),
                         "class_id": (o.get("classification") or {}).get("class_id"),
                         "label": (o.get("classification") or {}).get("label"),
                         "confidence": (o.get("classification") or {}).get("confidence")}
                        for i, o in sorted(rows.items())]}
