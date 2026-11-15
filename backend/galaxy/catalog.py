"""The 3D Atlas's per-object catalogue (``data/galaxy/catalog.npz``), shared by the API and scripts.

It is committed to the repository so a fresh clone works without rebuilding the map (which
downloads ~600k rows from Gaia and SIMBAD). To keep it small it is stored compactly:

* RA/Dec stay float64 (sub-milliarcsecond); other measurements are float32 (7 significant
  digits, far beyond their uncertainties);
* strings are UTF-8 bytes;
* columns that can be recomputed are not stored: distance = 1000/parallax, tangential
  velocity from proper motion and distance, and x, y, z (read from ``stars.bin``).

``load_catalog`` returns the full set of columns, exactly as the build computed them.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "data" / "galaxy" / "catalog.npz"


def public_galaxy_dir() -> Path:
    """Where the Atlas's static files are: the source tree in development, the built viewer in Docker."""
    dist = Path(os.environ.get("SPHEREX_FRONTEND_DIST", ROOT / "frontend" / "dist"))
    for d in (ROOT / "frontend" / "public" / "galaxy", dist / "galaxy"):
        if (d / "meta.json").exists():
            return d
    return ROOT / "frontend" / "public" / "galaxy"


STARS_BIN = public_galaxy_dir() / "stars.bin"
_FULL_PRECISION = {"ra", "dec"}
_DERIVED = {"dist", "vtan", "xyz"}


def save_catalog(path: Path = CATALOG_PATH, **cols: Any) -> Path:
    """Write the catalogue compactly (see module docstring)."""
    out: dict[str, np.ndarray] = {}
    for k, a in cols.items():
        if k in _DERIVED:
            continue
        a = np.asarray(a)
        if a.dtype.kind == "U":
            a = np.char.encode(a, "utf-8")
        elif a.dtype == np.float64 and k not in _FULL_PRECISION:
            a = a.astype(np.float32)
        elif k == "flags":
            a = a.astype(np.int32)
        out[k] = a
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **out)
    return path


def tangential_velocity(pmra: np.ndarray, pmdec: np.ndarray, dist_pc: np.ndarray) -> np.ndarray:
    """km/s from proper motion (mas/yr) and distance (pc)."""
    return 4.74047 * np.hypot(np.nan_to_num(pmra), np.nan_to_num(pmdec)) / 1000.0 * dist_pc


def load_catalog(path: Path = CATALOG_PATH, stars_bin: Path = STARS_BIN) -> dict[str, np.ndarray]:
    """All catalogue columns, with strings decoded and the derived columns recomputed."""
    if not path.exists():
        raise FileNotFoundError(path)
    with np.load(path, allow_pickle=False) as z:
        c = {k: z[k] for k in z.files}
    for k, a in list(c.items()):
        if a.dtype.kind == "S":
            c[k] = np.char.decode(a, "utf-8")
        elif a.dtype == np.float32:
            c[k] = a.astype(np.float64)
    n = len(c["ra"])
    with np.errstate(divide="ignore", invalid="ignore"):
        c["dist"] = 1000.0 / c["plx"]
    c["vtan"] = tangential_velocity(c["pmra"], c["pmdec"], c["dist"])
    if stars_bin.exists() and stars_bin.stat().st_size >= n * 12:
        c["xyz"] = np.fromfile(stars_bin, dtype=np.float32, count=n * 3).reshape(n, 3)
    else:
        c["xyz"] = np.full((n, 3), np.nan, np.float32)
    return c
