"""JPL Horizons as the reference for the Solar System, and as the corrector of the N-body engine.

Horizons serves the JPL DE441 planetary ephemeris (fitted to radar, spacecraft tracking and
optical observations). We use it two ways:
  * validation: start the N-body engine from Horizons states and compare later positions
    with Horizons, which gives the engine's real error;
  * correction ("override"): whenever a Horizons state exists for an epoch, it replaces the
    engine's state, so errors cannot accumulate past one correction interval.

Requests go to the public API (https://ssd.jpl.nasa.gov/api/horizons.api) and are cached on
disk under cache/horizons/ (no account, no personal data sent).
"""
from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

from .nbody import System

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache" / "horizons"
API = "https://ssd.jpl.nasa.gov/api/horizons.api"
AU_KM = 1.495978707e8
_KM3S2_TO_AU3D2 = 86400.0**2 / AU_KM**3

# GM (km^3/s^2) from JPL DE440 (Park et al. 2021). Earth and Moon are separate bodies (treating
# them as one point leaves ~1000 km/decade of error); other planets are planet+moons barycentres.
SOLAR_SYSTEM = [
    ("Sun", "10", 132712440041.279419),
    ("Mercury", "1", 22031.868551),
    ("Venus", "2", 324858.592000),
    ("Earth", "399", 398600.435507),
    ("Moon", "301", 4902.800118),
    ("Mars", "4", 42828.375816),
    ("Jupiter", "5", 126712764.100000),
    ("Saturn", "6", 37940584.841800),
    ("Uranus", "7", 5794556.400000),
    ("Neptune", "8", 6836527.100580),
    ("Pluto", "9", 975.500000),
]
# The four most massive asteroids (GM from DE440). They perturb Mars and Earth by hundreds of km per decade.
ASTEROIDS = [
    ("Ceres", "1;", 62.6284),
    ("Vesta", "4;", 17.288245),
    ("Pallas", "2;", 13.665878),
    ("Hygiea", "10;", 5.78),
]


def _bodies(asteroids: bool) -> list[tuple[str, str, float]]:
    return SOLAR_SYSTEM + (ASTEROIDS if asteroids else [])


def _get(params: dict[str, str]) -> dict:
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:20]
    f = CACHE / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    url = API + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    req = urllib.request.Request(url, headers={"User-Agent": "spherex-explorer-nbody-poc"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    if "result" not in data:
        raise RuntimeError(data.get("error", "Horizons returned no result"))
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def parse_vectors(result: str) -> np.ndarray:
    """Rows of (JD_TDB, x, y, z, vx, vy, vz) from a CSV VECTORS result."""
    if "$$SOE" not in result:
        raise RuntimeError(result[-400:])
    body = result.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
    rows = []
    for line in body.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        rows.append([float(parts[0])] + [float(x) for x in parts[2:8]])
    return np.array(rows)


def vectors(body_id: str, jd_start: float, jd_stop: float, step: str = "1d") -> np.ndarray:
    """Barycentric ICRF states (AU, AU/day) of one body from Horizons."""
    data = _get({
        "format": "json", "COMMAND": f"'{body_id}'", "EPHEM_TYPE": "VECTORS", "CENTER": "'500@0'",
        "START_TIME": f"'JD{jd_start:.6f}'", "STOP_TIME": f"'JD{jd_stop:.6f}'", "STEP_SIZE": f"'{step}'",
        "OUT_UNITS": "'AU-D'", "REF_PLANE": "'FRAME'", "VEC_TABLE": "'2'", "CSV_FORMAT": "'YES'",
        "OBJ_DATA": "'NO'", "MAKE_EPHEM": "'YES'", "TIME_TYPE": "'TDB'",
    })
    return parse_vectors(data["result"])


def solar_system_tables(jd_start: float, jd_stop: float, step: str, asteroids: bool = True) -> dict[str, np.ndarray]:
    return {name: vectors(bid, jd_start, jd_stop, step) for name, bid, _ in _bodies(asteroids)}


def system_at(tables: dict[str, np.ndarray], row: int, gr: bool = True) -> System:
    """Engine state taken from Horizons (row index into the tables)."""
    bodies = [b for b in _bodies(True) if b[0] in tables]
    names = [n for n, _, _ in bodies]
    mu = np.array([gm for _, _, gm in bodies]) * _KM3S2_TO_AU3D2
    r = np.array([tables[n][row, 1:4] for n in names])
    v = np.array([tables[n][row, 4:7] for n in names])
    s = System(names, mu, r, v, float(tables[names[0]][row, 0]), gr=gr)
    ast = [n for n, _, _ in ASTEROIDS if n in tables]
    s.notes += ["Solar System: Sun, Earth, Moon and 8 planet-system barycentres" + (f" + {', '.join(ast)}" if ast else "") + ", masses from JPL DE440",
                "omitted: " + ("" if ast else "asteroids, ") + "other asteroids, solar J2, tides, 1PN terms between planets" + ("" if gr else ", general relativity")]
    return s


def compare(s: System, tables: dict[str, np.ndarray], row: int) -> dict[str, float]:
    """Position error (km) of each body against Horizons."""
    return {n: float(np.linalg.norm(s.r[k] - tables[n][row, 1:4]) * AU_KM) for k, n in enumerate(s.names)}


def run_validation(jd_start: float, days: int, method: str = "dop853", gr: bool = True,
                   correct_every: int | None = None, step_days: int = 10, rk4_step: float = 0.25,
                   asteroids: bool = True) -> dict:
    """Integrate from Horizons states; compare every ``step_days``. With ``correct_every``,
    the engine is overridden by Horizons' state every that many days (the correction loop)."""
    tables = solar_system_tables(jd_start, jd_start + days, f"{step_days}d", asteroids)
    s = system_at(tables, 0, gr=gr)
    rows = len(tables["Sun"])
    e0 = _energy_rel(s)
    out = {"method": method, "gr": gr, "asteroids": asteroids, "correct_every_days": correct_every, "t0": s.t, "notes": s.notes,
           "samples": []}
    for k in range(1, rows):
        t = float(tables["Sun"][k, 0])
        s, _ = integrate_to(s, t, method, rk4_step)
        err = compare(s, tables, k)
        out["samples"].append({"jd": t, "days": t - out["t0"], "err_km": err})
        if correct_every and (t - out["t0"]) % correct_every < step_days / 2:
            s = system_at(tables, k, gr=gr)            # override with JPL's state
    out["energy_drift"] = abs(_energy_rel(s) - e0) / abs(e0)
    return out


def integrate_to(s: System, t: float, method: str, rk4_step: float):
    from .nbody import integrate
    return integrate(s, t, method=method, step=rk4_step)


def _energy_rel(s: System) -> float:
    from .nbody import energy
    return energy(s)
