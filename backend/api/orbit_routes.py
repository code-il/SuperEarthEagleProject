"""N-body orbit engine API (proof of concept).

GET /api/orbits/solar/validate  - integrate the Solar System from JPL Horizons states and report
                                  the engine's error against Horizons (optionally with the
                                  Horizons correction loop).
GET /api/orbits/system/{i}      - set up an exoplanet system from the NASA Exoplanet Archive,
                                  correct it until it reproduces the archive's transit
                                  ephemerides, and predict transits (N-body vs straight line).
"""
from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.orbits import exo, horizons

from .galaxy_routes import _systems

router = APIRouter(prefix="/api/orbits", tags=["orbit engine"])
_fits: dict[int, dict[str, Any]] = {}
_lock = threading.Lock()


@router.get("/solar/validate")
def solar_validate(
    days: int = Query(3650, ge=10, le=36525),
    start_jd: float = Query(2461041.5, description="TDB Julian date; default 2026-01-01"),
    method: str = Query("dop853", pattern="^(dop853|rk4)$"),
    gr: bool = True,
    asteroids: bool = True,
    correct_every: int | None = Query(None, ge=10, description="override with Horizons every N days"),
    step_days: int = Query(50, ge=1, le=1000),
) -> dict[str, Any]:
    try:
        r = horizons.run_validation(start_jd, days, method=method, gr=gr, correct_every=correct_every,
                                    step_days=step_days, asteroids=asteroids)
    except OSError as exc:
        raise HTTPException(503, f"JPL Horizons unreachable: {exc}") from exc
    names = list(r["samples"][0]["err_km"]) if r["samples"] else []
    r["max_err_km"] = {n: max(s["err_km"][n] for s in r["samples"]) for n in names}
    r["final_err_km"] = r["samples"][-1]["err_km"] if r["samples"] else {}
    r["reference"] = "JPL Horizons (DE441), barycentric ICRF"
    return r


def _fit(i: int) -> tuple[dict[str, Any], dict[str, Any]]:
    sys = _systems().get(i)
    if sys is None:
        raise HTTPException(404, "not a planet host")
    fit = _fits.get(i)
    if fit is None:
        fit = exo.fit_to_archive(sys)
        _fits[i] = fit
    return sys, fit


def _now_jd() -> float:
    return time.time() / 86400 + 2440587.5


@router.get("/system/{i}")
def system_nbody(i: int, days: float = Query(30, gt=0, le=365), from_jd: float | None = None) -> dict[str, Any]:
    with _lock:
        sys, fit = _fit(i)
        if not fit["ok"]:
            return {"host": sys["host"], "ok": False, "problems": fit["problems"], "notes": fit["notes"]}
        transits = exo.predict_transits(fit, from_jd if from_jd is not None else _now_jd(), days)
    return {
        "host": sys["host"], "ok": True, "converged": fit["converged"], "notes": fit["notes"],
        "correction": [{"iteration": h["iteration"], "worst_sigma": h["worst_sigma"], "planets": h["planets"]} for h in fit["iterations"]],
        "transits": transits,
        "model": MODEL,
    }


MODEL = "Newtonian N-body (DOP853), initial conditions corrected to the NASA Exoplanet Archive ephemerides"


@router.get("/system/{i}/positions")
def system_positions(i: int, from_jd: float | None = None, days: float = Query(120, gt=0, le=400),
                     samples_per_orbit: int = Query(20, ge=8, le=200)) -> dict[str, Any]:
    """Positions for the viewer. Only served when the corrected model reproduces the archive
    ephemerides (status "ok"); otherwise the status and reasons are returned and nothing is drawn."""
    with _lock:
        sys, fit = _fit(i)
        if not fit["ok"]:
            return {"host": sys["host"], "status": "refused", "problems": fit["problems"], "notes": fit["notes"]}
        if not fit["converged"]:
            best = min(h["worst_sigma"] for h in fit["iterations"])
            return {"host": sys["host"], "status": "not_converged", "notes": fit["notes"],
                    "problems": [f"the corrected model stays {best:.0f} sigma from the archive ephemerides"]}
        step = min(p["P"] for p in fit["planets"]) / samples_per_orbit
        data = exo.positions(fit, from_jd if from_jd is not None else _now_jd(), days, step)
    return {"host": sys["host"], "status": "ok", "notes": fit["notes"], "model": MODEL,
            "iterations": len(fit["iterations"]), "worst_sigma": fit["iterations"][-1]["worst_sigma"], **data}
