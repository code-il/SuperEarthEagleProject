"""Exoplanet systems in the N-body engine, set up and corrected from NASA Exoplanet Archive data.

Horizons has no exoplanets, so the "observations" that correct the engine are the archive's
measured transit ephemerides: for each planet, a mid-transit time T0 and a mean period P
(a straight-line fit to its observed transits). The engine:

1. starts every planet from its archive elements (P, e, omega, i, mass, star mass), with
   phases set from T0;
2. integrates the full N-body problem, so planets pull on each other and transit times
   wobble (transit-timing variations, TTVs);
3. fits a straight line to each planet's *simulated* transits around T0 and compares it with
   the archive's line; then corrects the planet's phase (shift along its orbit) and orbit size
   (to match the mean period), and repeats until the model reproduces the archive ephemeris
   within its uncertainty.

What it refuses to do: a planet without a measured mass, or with eccentricity but no omega,
makes the system unsuitable (N-body needs every mass and orbit), and the reason is returned.

Stated assumptions (returned with every result):
  * eccentricity not measured -> 0 (as the archive's circular fits assume);
  * the nodes' orientation around our line of sight (Omega) is unmeasurable from transits; all
    planets get Omega = 0, i.e. coplanar up to their measured inclinations. All-transiting
    systems are known to be nearly coplanar, but the exact mutual nodes are not measured;
  * star treated as a point mass; no GR, tides or spin.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq

from .nbody import MU_EARTH_PER_SUN, MU_SUN, System, elements_to_state, integrate, kepler_advance

DEG = np.pi / 180


def _num(v):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else float(v)


def check(sys: dict) -> tuple[list[dict], list[str], list[str]]:
    """Usable planets (sorted by period), reasons it cannot be done, and assumptions made."""
    problems, notes, planets = [], [], []
    if _num(sys.get("st_mass")) is None:
        problems.append("star mass not measured")
    for p in sorted(sys["planets"], key=lambda p: p.get("period_d") or 1e9):
        name = p["name"]
        P, m, T0 = _num(p.get("period_d")), _num(p.get("mass_me")), _num(p.get("t_transit"))
        e, ee, w, inc = _num(p.get("ecc")), _num(p.get("ecc_err")), _num(p.get("omega_deg")), _num(p.get("incl_deg"))
        if P is None:
            problems.append(f"{name}: period not measured"); continue
        if m is None:
            problems.append(f"{name}: mass not measured"); continue
        if T0 is None:
            problems.append(f"{name}: no transit time"); continue
        e_meas = e is not None and (e > 0 or (ee is not None and ee > 0))
        if e_meas and e > 0 and w is None:
            problems.append(f"{name}: eccentricity measured but not omega"); continue
        if not e_meas:
            notes.append(f"{name}: eccentricity not measured, set to 0")
        if inc is None:
            notes.append(f"{name}: inclination not measured, set to 90 deg")
        # An uncertainty of 0 in the archive means "not given"; floor it so it is not treated as exact.
        planets.append({"name": name, "P": P, "m": m, "T0": T0, "T0_err": max(_num(p.get("t_transit_err")) or 0, 1e-4),
                        "P_err": max(_num(p.get("period_err")) or 0.0, 1e-6), "e": e if e_meas else 0.0,
                        "w": (w if (e_meas and w is not None) else 90.0) * DEG, "i": (inc if inc is not None else 90.0) * DEG})
    # Physical consistency: the composite table mixes papers, which can give orbits that cross or
    # sit closer than the two-planet Hill stability limit (Gladman 1993: 2*sqrt(3) mutual Hill radii).
    Ms = _num(sys.get("st_mass"))
    if Ms and not problems:
        el = sorted(((Ms * (p["P"] / 365.25) ** 2) ** (1 / 3), p) for p in planets) if planets else []
        for (a1, p1), (a2, p2) in zip(el, el[1:]):
            gap = a2 * (1 - p2["e"]) - a1 * (1 + p1["e"])
            rh = ((p1["m"] + p2["m"]) * MU_EARTH_PER_SUN / (3 * Ms)) ** (1 / 3) * (a1 + a2) / 2
            if gap <= 0:
                problems.append(f"{p1['name']} and {p2['name']}: archive elements give crossing orbits (inconsistent composite parameters)")
            elif gap < 2 * np.sqrt(3) * rh:
                problems.append(f"{p1['name']} and {p2['name']}: closer than the Hill stability limit with the archive's eccentricities")
    if len(planets) >= 2:
        notes.append("node longitudes unmeasurable from transits: all set to 0 (coplanar up to the measured inclinations)")
    notes.append("star as a point mass; no relativity, tides or spin")
    return planets, problems, notes


def build(sys: dict, planets: list[dict], t0: float) -> System:
    mu_s = sys["st_mass"] * MU_SUN
    names, mu, r, v = [sys["host"]], [mu_s], [np.zeros(3)], [np.zeros(3)]
    for p in planets:
        mu_p = p["m"] * MU_EARTH_PER_SUN * MU_SUN
        mtot = mu_s + mu_p
        a = (mtot * (p["P"] / (2 * np.pi)) ** 2) ** (1 / 3)
        f_tr = np.pi / 2 - p["w"]                       # true anomaly at mid-transit
        E_tr = 2 * np.arctan2(np.sqrt(1 - p["e"]) * np.sin(f_tr / 2), np.sqrt(1 + p["e"]) * np.cos(f_tr / 2))
        M = E_tr - p["e"] * np.sin(E_tr) + 2 * np.pi * (t0 - p["T0"]) / p["P"]
        ri, vi = elements_to_state(mtot, a, p["e"], p["i"], p["w"], 0.0, M)
        names.append(p["name"]); mu.append(mu_p); r.append(ri); v.append(vi)
    mu, r, v = np.array(mu), np.array(r), np.array(v)
    # Move to the barycentre frame.
    r -= (mu[:, None] * r).sum(0) / mu.sum()
    v -= (mu[:, None] * v).sum(0) / mu.sum()
    return System(names, mu, r, v, t0)


def transit_times(sol, k: int, n_bodies: int, t_lo: float, t_hi: float, P: float) -> np.ndarray:
    """Mid-transit times of body k (planet in front of the star, minimum sky separation)."""
    def rel(t):
        y = sol(t)
        r = y[: 3 * n_bodies].reshape(n_bodies, 3); v = y[3 * n_bodies:].reshape(n_bodies, 3)
        return r[k] - r[0], v[k] - v[0]

    def g(t):
        dr, dv = rel(t)
        return dr[0] * dv[0] + dr[1] * dv[1]

    ts = np.arange(t_lo, t_hi, P / 40)
    Y = sol(ts)                                        # vectorised dense evaluation
    dr = Y[3 * k: 3 * k + 3] - Y[0:3]
    dv = Y[3 * n_bodies + 3 * k: 3 * n_bodies + 3 * k + 3] - Y[3 * n_bodies: 3 * n_bodies + 3]
    gs = dr[0] * dv[0] + dr[1] * dv[1]
    out = []
    for j in np.nonzero((gs[:-1] < 0) & (gs[1:] >= 0))[0]:
        t = brentq(g, ts[j], ts[j + 1], xtol=1e-9)
        if rel(t)[0][2] > 0:                           # in front of the star, not behind it
            out.append(t)
    return np.array(out)


def _ephemeris_fit(times: np.ndarray, T0: float, P: float) -> tuple[float, float]:
    n = np.round((times - T0) / P)
    A = np.vstack([np.ones_like(n), n]).T
    (c, slope), *_ = np.linalg.lstsq(A, times, rcond=None)
    return c, slope                                   # model T0 at epoch 0, model mean period


def fit_to_archive(sys: dict, baseline: float = 600.0, max_iter: int = 15, damping: float = 0.6) -> dict:
    """Set up the system and correct it until it reproduces the archive ephemerides."""
    planets, problems, notes = check(sys)
    if problems:
        return {"ok": False, "host": sys["host"], "problems": problems, "notes": notes}
    t_lo = min(p["T0"] for p in planets) - baseline / 2
    t_hi = max(p["T0"] for p in planets) + baseline / 2
    s0 = build(sys, planets, t_lo)
    n = len(s0.mu)
    history = []
    for it in range(max_iter):
        _, sol = integrate(s0, t_hi, dense=True, rtol=1e-10, atol=1e-12)
        worst = 0.0
        rows = []
        for k, p in enumerate(planets, start=1):
            tt = transit_times(sol, k, n, max(t_lo, p["T0"] - baseline / 2), min(t_hi, p["T0"] + baseline / 2), p["P"])
            c, Pm = _ephemeris_fit(tt, p["T0"], p["P"])
            dT, dP = c - p["T0"], Pm - p["P"]
            rows.append({"planet": p["name"], "dT0_min": float(dT * 1440), "dP_s": float(dP * 86400),
                         "T0_err_min": p["T0_err"] * 1440, "P_err_s": p["P_err"] * 86400})
            worst = max(worst, abs(dT) / p["T0_err"], abs(dP) / max(p["P_err"], 1e-7))
            # Correction: orbit size so the mean period matches, then phase so T0 matches.
            mu_rel = s0.mu[0] + s0.mu[k]
            dr, dv = s0.r[k] - s0.r[0], s0.v[k] - s0.v[0]
            # Damped (fraction ``damping`` of the full step): planets in or near resonance pull on
            # each other's periods, and full steps overshoot.
            scale = (p["P"] / Pm) ** (2 / 3 * damping)
            dr, dv = dr * scale, dv / np.sqrt(scale)
            dr, dv = kepler_advance(mu_rel, dr, dv, damping * dT)
            s0.r[k], s0.v[k] = s0.r[0] + dr, s0.v[0] + dv
        history.append({"iteration": it, "worst_sigma": float(worst), "planets": rows})
        if worst < 1.0:
            break
        # Stop when the correction is not converging (typical of resonant chains, which need a
        # joint fit to individual transit times rather than one ephemeris per planet).
        if it >= 3 and worst > min(h["worst_sigma"] for h in history[:-1]) * 2:
            break
    converged = bool(history[-1]["worst_sigma"] < 1.0)
    s0.notes = notes
    if not converged:
        notes = notes + [f"correction did not converge (best {min(h['worst_sigma'] for h in history):.1f} sigma): "
                         "predictions are NOT consistent with the archive ephemeris"]
    return {"ok": True, "converged": converged, "host": sys["host"], "system": s0, "iterations": history, "notes": notes,
            "planets": planets, "t_hi": t_hi}


def state_at(fit: dict, jd: float) -> System:
    """The corrected system advanced to ``jd``. Keeps a cursor so later requests continue from
    the last computed time instead of re-integrating from the fit epoch (years earlier)."""
    cur: System = fit.get("cursor") or fit["system"]
    if cur.t > jd:
        cur = fit["system"]
    s, _ = integrate(cur, jd, rtol=1e-11, atol=1e-13)
    fit["cursor"] = s
    return s


def positions(fit: dict, jd_from: float, days: float, step: float) -> dict:
    """Planet positions and velocities relative to the star (AU, AU/day) on a regular time grid,
    in the frame (line of nodes, up on the sky, towards the observer)."""
    s = state_at(fit, jd_from)
    n = len(s.mu)
    _, sol = integrate(s, jd_from + days, dense=True, rtol=1e-11, atol=1e-13)
    ts = jd_from + np.arange(int(np.floor(days / step)) + 1) * step
    Y = sol(ts)
    out = []
    for k in range(1, n):
        r = (Y[3 * k: 3 * k + 3] - Y[0:3]).T
        v = (Y[3 * n + 3 * k: 3 * n + 3 * k + 3] - Y[3 * n: 3 * n + 3]).T
        out.append({"name": s.names[k], "pos": np.round(r, 12).ravel().tolist(), "vel": np.round(v, 12).ravel().tolist()})
    return {"t0": float(ts[0]), "step": float(step), "n": int(len(ts)), "planets": out}


def predict_transits(fit: dict, jd_from: float, days: float) -> list[dict]:
    """N-body transit predictions vs the archive's straight-line ephemeris, with its 1 sigma."""
    s0 = state_at(fit, jd_from)
    n = len(s0.mu)
    _, sol = integrate(s0, jd_from + days, dense=True, rtol=1e-11, atol=1e-13)
    out = []
    for k, p in enumerate(fit["planets"], start=1):
        for t in transit_times(sol, k, n, jd_from, jd_from + days, p["P"]):
            e = np.round((t - p["T0"]) / p["P"])
            lin = p["T0"] + e * p["P"]
            sig = np.hypot(p["T0_err"], e * p["P_err"])
            out.append({"planet": p["name"], "epoch": int(e), "nbody_jd": float(t), "linear_jd": float(lin),
                        "ttv_min": float((t - lin) * 1440), "linear_sigma_min": float(sig * 1440)})
    return sorted(out, key=lambda r: r["nbody_jd"])
