"""Angular motion from positions at several times (sections B and C).

Positions are projected on a tangent plane about their weighted mean
(xi = east, eta = north, arcsec) and fitted, with per-point uncertainties,
by weighted least squares:

* **static** model   x(t) = x0                         (2 parameters)
* **linear** model   x(t) = x0 + mu (t - t_ref)        (4)  -> proper motion
* **quadratic**      ... + a (t - t_ref)^2 / 2         (6)  -> curvature test

If a parallax is given (e.g. from a catalogue match) its annual
displacement, for the Earth's barycentric position at each time, is
removed first. SPHEREx is in low Earth orbit, so the Earth's position is
appropriate to << 1 mas for stars.

Statistics reported:
* ``motion_significance``: Delta chi^2 (static - linear), 2 d.o.f., as a
  p-value and the equivalent Gaussian sigma. This is the test for "has it
  moved", independent of how big the motion is.
* ``pm_snr``: |mu| / sigma_mu.
* ``linear_chi2_red`` and the quadratic F-test for non-linear apparent
  motion (an orbiting / solar-system / mismatched source, or wrong errors).
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np
from scipy import stats

from backend.data_acquisition.coords import parse_time

DAYS_PER_YEAR = 365.25


def _mjd(t: Any) -> float:
    if isinstance(t, (int, float, np.floating)) and 10000 < float(t) < 100000:
        return float(t)
    return float(parse_time(t).mjd)


def to_tangent(ra: np.ndarray, dec: np.ndarray, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    """Gnomonic projection, arcsec (xi east, eta north)."""
    a, d, a0, d0 = map(np.radians, (ra, dec, ra0, dec0))
    cosc = np.sin(d0) * np.sin(d) + np.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / cosc
    eta = (np.cos(d0) * np.sin(d) - np.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
    return np.degrees(xi) * 3600, np.degrees(eta) * 3600


def from_tangent(xi: np.ndarray, eta: np.ndarray, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    x, y = np.radians(np.asarray(xi) / 3600), np.radians(np.asarray(eta) / 3600)
    a0, d0 = np.radians(ra0), np.radians(dec0)
    rho = np.hypot(x, y)
    c = np.arctan(rho)
    with np.errstate(invalid="ignore", divide="ignore"):
        dec = np.where(rho == 0, d0, np.arcsin(np.cos(c) * np.sin(d0) + y * np.sin(c) * np.cos(d0) / np.where(rho == 0, 1, rho)))
        ra = a0 + np.arctan2(x * np.sin(c), rho * np.cos(d0) * np.cos(c) - y * np.sin(d0) * np.sin(c))
    return np.degrees(ra) % 360, np.degrees(dec)


def separation_arcsec(ra1, dec1, ra2, dec2) -> np.ndarray:
    a1, d1, a2, d2 = map(np.radians, (ra1, dec1, ra2, dec2))
    h = np.sin((d2 - d1) / 2) ** 2 + np.cos(d1) * np.cos(d2) * np.sin((a2 - a1) / 2) ** 2
    return np.degrees(2 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))) * 3600


def parallax_factors(ra: float, dec: float, mjds: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    """(f_ra*, f_dec) such that the parallactic shift is parallax * f (same units)."""
    from astropy.coordinates import get_body_barycentric
    from astropy.time import Time

    pos = get_body_barycentric("earth", Time(np.asarray(mjds, dtype=float), format="mjd", scale="tdb"))
    x, y, z = (pos.x.to_value("au"), pos.y.to_value("au"), pos.z.to_value("au"))
    a, d = math.radians(ra), math.radians(dec)
    f_ra = x * math.sin(a) - y * math.cos(a)
    f_dec = x * math.cos(a) * math.sin(d) + y * math.sin(a) * math.sin(d) - z * math.cos(d)
    return f_ra, f_dec


def _errors(uncertainties: Any, n: int) -> tuple[np.ndarray, np.ndarray]:
    if uncertainties is None:
        return np.ones(n), np.ones(n)
    u = np.asarray(uncertainties, dtype=float)
    if u.ndim == 0:
        return np.full(n, float(u)), np.full(n, float(u))
    if u.ndim == 1:
        return u.copy(), u.copy()
    return u[:, 0].copy(), u[:, 1].copy()


def _wls(t: np.ndarray, y: np.ndarray, s: np.ndarray, degree: int):
    a = np.vander(t, degree + 1, increasing=True)
    if degree == 2:
        a[:, 2] *= 0.5
    w = 1.0 / s
    aw = a * w[:, None]
    coef, *_ = np.linalg.lstsq(aw, y * w, rcond=None)
    cov = np.linalg.pinv(aw.T @ aw)
    chi2 = float((((a @ coef) - y) * w) ** 2 @ np.ones(len(y)))
    return coef, cov, chi2


def calculate_angular_motion(source_positions: Sequence[Sequence[float]], timestamps: Iterable[Any],
                             uncertainties: Any = None, *, parallax_mas: float | None = None,
                             systematic_arcsec: float = 0.0) -> dict[str, Any]:
    """Proper motion and motion statistics from positions at several times.

    ``source_positions``: [(ra_deg, dec_deg), ...]; ``timestamps``: MJD,
    ISO strings or ``astropy.time.Time``; ``uncertainties`` in arcsec: a
    scalar, one per point, or [(sigma_ra_cosdec, sigma_dec), ...].
    ``systematic_arcsec`` is added in quadrature to every point
    (registration error). Returns motion (mas/yr), uncertainties, the
    displacement between first and last point and the model statistics.
    """
    pos = np.asarray(source_positions, dtype=float)
    mjd = np.array([_mjd(t) for t in timestamps], dtype=float)
    n = len(mjd)
    if pos.shape != (n, 2):
        raise ValueError("source_positions must be (n, 2) matching timestamps")
    sx, sy = _errors(uncertainties, n)
    sx = np.hypot(sx, systematic_arcsec)
    sy = np.hypot(sy, systematic_arcsec)
    order = np.argsort(mjd)
    pos, mjd, sx, sy = pos[order], mjd[order], sx[order], sy[order]

    wx, wy = 1 / sx ** 2, 1 / sy ** 2
    ra0 = float(np.degrees(np.angle(np.sum(np.exp(1j * np.radians(pos[:, 0])) * wx))) % 360)
    dec0 = float(np.sum(pos[:, 1] * wy) / wy.sum())
    xi, eta = to_tangent(pos[:, 0], pos[:, 1], ra0, dec0)
    if parallax_mas:
        fa, fd = parallax_factors(ra0, dec0, mjd)
        xi = xi - parallax_mas / 1000 * fa
        eta = eta - parallax_mas / 1000 * fd
    tref = float(np.sum(mjd * (wx + wy)) / np.sum(wx + wy))
    t = (mjd - tref) / DAYS_PER_YEAR
    baseline = float((mjd[-1] - mjd[0]) / DAYS_PER_YEAR) if n else 0.0

    out: dict[str, Any] = {"n_points": n, "baseline_yr": baseline, "ref_mjd": tref,
                           "parallax_removed_mas": parallax_mas or None,
                           "systematic_arcsec": systematic_arcsec}
    # displacement first -> last (with uncertainty)
    if n >= 2:
        d = float(separation_arcsec(pos[0, 0], pos[0, 1], pos[-1, 0], pos[-1, 1]))
        dxi, deta = xi[-1] - xi[0], eta[-1] - eta[0]
        derr = math.sqrt(((dxi * math.hypot(sx[0], sx[-1])) ** 2 + (deta * math.hypot(sy[0], sy[-1])) ** 2)
                         / max(dxi ** 2 + deta ** 2, 1e-12))
        out.update(displacement_arcsec=d, displacement_err_arcsec=derr,
                   displacement_snr=d / derr if derr > 0 else None,
                   displacement_pa_deg=float(math.degrees(math.atan2(dxi, deta)) % 360))
    if n < 2 or len(np.unique(np.round(mjd, 3))) < 2 or baseline <= 0:
        out.update(model="insufficient_data", pm_ra_cosdec_mas_yr=None, pm_dec_mas_yr=None,
                   pm_total_mas_yr=None, pm_err_mas_yr=None)
        return out

    chi_static = 0.0
    fits = {}
    for axis, (y, s) in {"x": (xi, sx), "y": (eta, sy)}.items():
        w = 1 / s ** 2
        chi_static += float(np.sum(w * (y - np.sum(w * y) / w.sum()) ** 2))
        fits[axis] = _wls(t, y, s, 1)
    chi_lin = fits["x"][2] + fits["y"][2]
    mux, muy = fits["x"][0][1], fits["y"][0][1]  # arcsec/yr
    ex, ey = math.sqrt(fits["x"][1][1, 1]), math.sqrt(fits["y"][1][1, 1])
    dof_lin = 2 * n - 4
    chi2r = chi_lin / dof_lin if dof_lin > 0 else None
    # Errors scaled up (never down) when the linear model fits worse than the errors say.
    scale = math.sqrt(max(1.0, chi2r)) if chi2r is not None else 1.0
    mu = math.hypot(mux, muy)
    mu_err = math.sqrt((mux * ex) ** 2 + (muy * ey) ** 2) / mu if mu > 0 else math.hypot(ex, ey) / math.sqrt(2)
    dchi = max(chi_static - chi_lin, 0.0)
    p_static = float(stats.chi2.sf(dchi / scale ** 2, 2))
    out.update(
        model="linear",
        pm_ra_cosdec_mas_yr=mux * 1000, pm_dec_mas_yr=muy * 1000, pm_total_mas_yr=mu * 1000,
        pm_ra_cosdec_err_mas_yr=ex * scale * 1000, pm_dec_err_mas_yr=ey * scale * 1000,
        pm_err_mas_yr=mu_err * scale * 1000,
        pm_pa_deg=float(math.degrees(math.atan2(mux, muy)) % 360),
        pm_snr=mu / (mu_err * scale) if mu_err > 0 else None,
        ra_at_ref=None, dec_at_ref=None,
        static_chi2=chi_static, linear_chi2=chi_lin, linear_dof=dof_lin, linear_chi2_red=chi2r,
        error_scale=scale, motion_p_value=p_static,
        motion_sigma=float(min(stats.norm.isf(max(p_static, 1e-300) / 2), 38.0)),
    )
    ra_ref, dec_ref = from_tangent(fits["x"][0][0], fits["y"][0][0], ra0, dec0)
    out["ra_at_ref"], out["dec_at_ref"] = float(ra_ref), float(dec_ref)
    out["position_err_at_ref_arcsec"] = [math.sqrt(fits["x"][1][0, 0]) * scale, math.sqrt(fits["y"][1][0, 0]) * scale]
    res_x = xi - (fits["x"][0][0] + mux * t)
    res_y = eta - (fits["y"][0][0] + muy * t)
    out["residuals_arcsec"] = [[float(a), float(b)] for a, b in zip(res_x, res_y)]
    out["rms_residual_arcsec"] = float(np.sqrt(np.mean(res_x ** 2 + res_y ** 2)))

    # Non-linear apparent motion: does a quadratic term improve the fit?
    n_times = len(np.unique(np.round(mjd, 1)))
    if n_times >= 4 and dof_lin > 2:
        chi_q = sum(_wls(t, y, s, 2)[2] for y, s in ((xi, sx), (eta, sy)))
        dof_q = 2 * n - 6
        if dof_q > 0 and chi_q > 0:
            f = ((chi_lin - chi_q) / 2) / (chi_q / dof_q)
            p_q = float(stats.f.sf(max(f, 0), 2, dof_q))
            out.update(quadratic_chi2=chi_q, nonlinear_f=f, nonlinear_p_value=p_q)
            if p_q < 1e-3 and (chi2r or 0) > 3:
                out["model"] = "non_linear"
    out["mjd"] = [float(m) for m in mjd]
    return out


def predict_position(model: dict[str, Any], mjd: float) -> tuple[float, float]:
    """Position at ``mjd`` from a linear motion model.

    Accepts the output of ``calculate_angular_motion`` (``ra_at_ref``,
    ``dec_at_ref``, ``ref_mjd``) or a catalogue-style dict (``ra``, ``dec``,
    ``ref_mjd``, ``pm_ra_cosdec_mas_yr``, ``pm_dec_mas_yr``). Linear in the
    tangent plane; parallax not applied.
    """
    ra = model.get("ra_at_ref", model.get("ra"))
    dec = model.get("dec_at_ref", model.get("dec"))
    pa, pd, ref = model.get("pm_ra_cosdec_mas_yr"), model.get("pm_dec_mas_yr"), model.get("ref_mjd")
    if ra is None or dec is None:
        raise ValueError("model has no position")
    if pa is None or pd is None or ref is None:
        return float(ra), float(dec)
    dt = (mjd - ref) / DAYS_PER_YEAR
    r, d = from_tangent(pa / 1000 * dt, pd / 1000 * dt, ra, dec)
    return float(r), float(d)
