"""Light curves, spectra and variability (sections D and E).

The central difficulty: **every SPHEREx frame is taken at a different
wavelength** (the linear variable filter puts each sky position on a
different channel in each exposure). A plain "flux vs time" plot therefore
mixes the source's spectrum with its variability. This module separates
them:

* ``generate_light_curve`` does forced PSF photometry at the source's
  (moving) position in every frame and returns flux and AB magnitude vs
  time, each point tagged with its wavelength.
* ``variability_indicator`` fits one smooth mean spectrum (a low-order
  polynomial in ln(lambda)) shared by all visits, plus one scale factor
  per visit. Variability = visit scale factors inconsistent with 1, with a
  calibration-repeatability floor added to every scale-factor error.
* ``generate_spectrum`` gives flux vs wavelength per visit (each visit
  samples many channels) and a channel-binned combined spectrum; it is a
  multi-channel profile of the PSF-fitted flux, not a calibrated
  spectrum (see README limitations).
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
from scipy import stats

from backend.image_processing.channels import channel_for

from .detection import ab_mag, extract_source_photometry
from .motion import predict_position

CALIBRATION_FLOOR = 0.03
"""Fractional flux error added in quadrature per visit (relative calibration, flat field, PSF/aperture)."""
VARIABILITY_P_THRESHOLD = 1e-3
MIN_POINTS_PER_VISIT = 3
MIN_VISIT_SNR = 5.0
UNIFORM_RELEASE_SET = {"DR1", "DR1_DEEP", "QR3", "QR3_DEEP"}
"""IRSA: "The DR1 spectral images are intended to be used together with the QR3 spectral images as a uniformly processed set."."""
"""Each visit's flux scale must be measured at >= 5 sigma before a change is called variability."""
BAD_PHOT_FLAGS = {"off_frame", "masked_core", "insufficient_pixels", "saturation_nearby", "partially_masked"}


def position_model(source: dict[str, Any]):
    """Callable mjd -> (ra, dec) from a source's motion (or fixed position)."""
    motion = source.get("angular_motion") or {}
    if motion.get("ra_at_ref") is not None and motion.get("pm_ra_cosdec_mas_yr") is not None:
        return lambda mjd: predict_position(motion, mjd)
    visits = source.get("visit_positions") or []
    if visits:
        def nearest(mjd):
            v = min(visits, key=lambda p: abs(p["mjd"] - mjd))
            return v["ra"], v["dec"]
        return nearest
    return lambda mjd: (source["ra"], source["dec"])


def forced_photometry(source: dict[str, Any], observations: Sequence[Any], epochs: dict[str, int] | None = None
                      ) -> list[dict[str, Any]]:
    """Forced PSF photometry of one source in every frame (position follows its motion)."""
    pos = position_model(source)
    out = []
    for o in observations:
        if o.mjd is None:
            continue
        ra, dec = pos(o.mjd)
        rec = extract_source_photometry(o, [{"ra": ra, "dec": dec, "id": source.get("source_id")}])[0]
        rec["epoch"] = (epochs or {}).get(o.observation_id)
        rec["good"] = rec["flux_mjy"] is not None and not (set(rec["quality_flags"]) & BAD_PHOT_FLAGS)
        m, me = ab_mag(rec["flux_mjy"], rec["flux_err_mjy"]) if rec["flux_mjy"] is not None else (None, None)
        rec["mag_ab"], rec["mag_ab_err"] = m, me
        out.append(rec)
    return out


def generate_light_curve(source_id: str, observations: Sequence[Any], *, source: dict[str, Any] | None = None,
                         epochs: dict[str, int] | None = None, photometry: list[dict[str, Any]] | None = None
                         ) -> dict[str, Any]:
    """Flux (mJy) and AB magnitude vs time for one source.

    ``observations``: processed frames. ``source``: the analysed source
    (position/motion); or pass precomputed ``photometry``. Points with
    unreliable photometry (masked/saturated core, off frame) are returned
    with ``good=False`` and excluded from ``points`` used for plotting.
    """
    phot = photometry if photometry is not None else forced_photometry(source or {}, observations, epochs)
    good = [p for p in phot if p["good"]]
    points = [{"mjd": p["mjd"], "observation_time": p["observation_time"], "observation_id": p["observation_id"],
               "epoch": p["epoch"], "wavelength": p["wavelength"], "detector": p["detector"],
               "data_release": p.get("data_release"),
               "flux": p["flux_mjy"], "flux_err": p["flux_err_mjy"],
               "aperture_flux": p["aperture_flux_mjy"], "mag_ab": p["mag_ab"], "mag_ab_err": p["mag_ab_err"],
               "snr": p["snr"]} for p in sorted(good, key=lambda q: q["mjd"])]
    n_bad = len(phot) - len(good)
    flags: dict[str, int] = {}
    for p in phot:
        for f in p["quality_flags"]:
            flags[f] = flags.get(f, 0) + 1
    return {"source_id": source_id, "units": "mJy", "magnitude_system": "AB", "points": points,
            "n_frames": len(phot), "n_good": len(good), "n_rejected": n_bad, "rejected_flags": flags,
            "warning": ("Each point is at a different wavelength (colour = wavelength). Changes in flux "
                        "between points reflect the spectrum as well as time; use variability_indicator.")}


# --------------------------------------------------------------------------
# Variability
# --------------------------------------------------------------------------
def _poly_design(lnl: np.ndarray, deg: int) -> np.ndarray:
    return np.vander(lnl - lnl.mean(), deg + 1, increasing=True)


def variability_indicator(points: list[dict[str, Any]], *, calibration_floor: float = CALIBRATION_FLOOR,
                          degree: int = 2) -> dict[str, Any]:
    """Visit-to-visit variability with the spectral shape removed.

    Model: F(lambda, visit) = s_visit * P(ln lambda), P a polynomial of
    ``degree`` shared by all visits, solved by alternating least squares.
    Returns scale factors per visit, chi^2 against "no variability"
    (all s = 1), p-value, peak-to-peak amplitude and the wavelength overlap
    between visits. ``reliable`` is False when visits do not overlap in
    wavelength or have too few points (then the polynomial extrapolates).
    """
    pts = [p for p in points if p.get("flux") is not None and p.get("flux_err") and p.get("wavelength")
           and p.get("epoch") is not None]
    by: dict[int, list[dict[str, Any]]] = {}
    for p in pts:
        by.setdefault(p["epoch"], []).append(p)
    visits = {e: v for e, v in by.items() if len(v) >= MIN_POINTS_PER_VISIT}
    out: dict[str, Any] = {"method": f"visit scale factors on a shared ln(lambda) polynomial (degree {degree})",
                           "calibration_floor": calibration_floor, "n_visits": len(visits),
                           "n_points": sum(len(v) for v in visits.values())}
    if len(visits) < 2:
        out.update(reliable=False, reason="fewer than two visits with enough good photometry",
                   chi2=None, p_value=None, amplitude=None, significant=False)
        return out
    flat = [p for e in sorted(visits) for p in visits[e]]
    lam = np.array([p["wavelength"] for p in flat])
    f = np.array([p["flux"] for p in flat])
    s = np.array([p["flux_err"] for p in flat])
    ep = np.array([p["epoch"] for p in flat])
    keys = sorted(visits)
    lnl = np.log(lam)
    deg = min(degree, max(0, len(set(np.round(lam, 3))) - 2))
    a = _poly_design(lnl, deg)
    scale = {e: 1.0 for e in keys}
    for _ in range(20):
        sv = np.array([scale[e] for e in ep])
        coef, *_ = np.linalg.lstsq(a * (sv / s)[:, None], f / s, rcond=None)
        model = a @ coef
        new = {}
        for e in keys:
            m = ep == e
            w = 1 / s[m] ** 2
            new[e] = float(np.sum(w * f[m] * model[m]) / np.sum(w * model[m] ** 2))
        norm = float(np.average(list(new.values()), weights=[(ep == e).sum() for e in keys]))
        new = {e: v / norm for e, v in new.items()}
        if max(abs(new[e] - scale[e]) for e in keys) < 1e-6:
            scale = new
            break
        scale = new
    sv = np.array([scale[e] for e in ep])
    coef, *_ = np.linalg.lstsq(a * (sv / s)[:, None], f / s, rcond=None)
    model = a @ coef
    rows, sig = [], []
    for e in keys:
        m = ep == e
        w = 1 / s[m] ** 2
        se_stat = float(1 / math.sqrt(np.sum(w * model[m] ** 2)))
        resid = (f[m] - scale[e] * model[m]) / s[m]
        chi_in = float(np.sum(resid ** 2) / max(m.sum() - 1, 1))
        se = math.hypot(se_stat * math.sqrt(max(chi_in, 1.0)), calibration_floor)
        sig.append(se)
        rows.append({"epoch": int(e), "scale": round(scale[e], 4), "scale_err": round(se, 4), "n_points": int(m.sum()),
                     "mjd": float(np.mean([p["mjd"] for p in visits[e]])),
                     "wavelength_range": [float(lam[m].min()), float(lam[m].max())],
                     "within_visit_chi2_red": round(chi_in, 2),
                     "releases": sorted({p.get("data_release") for p in visits[e] if p.get("data_release")})})
    sc = np.array([r["scale"] for r in rows])
    se = np.array(sig)
    w = 1 / se ** 2
    mean = float(np.sum(w * sc) / w.sum())
    chi2 = float(np.sum(((sc - mean) / se) ** 2))
    dof = len(sc) - 1
    p = float(stats.chi2.sf(chi2, dof))
    lo = max(r["wavelength_range"][0] for r in rows)
    hi = min(r["wavelength_range"][1] for r in rows)
    span = lam.max() - lam.min()
    overlap = max(0.0, hi - lo) / span if span > 0 else 1.0
    amp = float(sc.max() - sc.min())
    min_visit_snr = float(min(r["scale"] / r["scale_err"] for r in rows))
    reliable = overlap >= 0.3
    out.update(visits=rows, chi2=round(chi2, 2), dof=dof, p_value=p,
               sigma=float(min(stats.norm.isf(max(p, 1e-300) / 2), 38.0)), amplitude=round(amp, 4),
               amplitude_mag=round(2.5 * math.log10(sc.max() / sc.min()), 3) if sc.min() > 0 else None,
               wavelength_overlap=round(overlap, 2), reliable=reliable, min_visit_snr=round(min_visit_snr, 2),
               significant=bool(reliable and p < VARIABILITY_P_THRESHOLD and amp > 3 * calibration_floor
                                and min_visit_snr >= MIN_VISIT_SNR),
               mean_spectrum_coefficients=[float(c) for c in coef])
    if not reliable:
        out["reason"] = "visits cover different wavelengths; the shared spectrum is extrapolated"
    rels = sorted({r for row in rows for r in row["releases"]})
    out["releases"] = rels
    if len(rels) > 1:
        uniform = set(rels) <= UNIFORM_RELEASE_SET
        out["release_note"] = (f"Visits come from {', '.join(rels)}. " + (
            "IRSA documents DR1 and QR3 as one uniformly processed set." if uniform else
            "These releases were calibrated differently: compare visits within one release only."))
        if not uniform:
            out["reliable"] = False
            out["significant"] = False
    out["warning"] = ("Apparent variability can be instrumental: different wavelengths, detector "
                      "positions, PSF/undersampling phase, background and calibration between visits. "
                      "Treat it as a candidate until repeated in another channel or instrument.")
    return out


# --------------------------------------------------------------------------
# Spectra
# --------------------------------------------------------------------------
def generate_spectrum(source_id: str, spectral_data: list[dict[str, Any]], *, by_visit: bool = True) -> dict[str, Any]:
    """Wavelength vs flux for one source from its photometry points.

    ``spectral_data``: light-curve points (``wavelength``, ``flux``,
    ``flux_err``, ``epoch``, ``detector``). Returns the per-visit spectra
    (raw points) and a combined spectrum binned on the 102 SPHEREx channels
    (inverse-variance mean per channel).
    """
    pts = [p for p in spectral_data if p.get("flux") is not None and p.get("wavelength")]
    out: dict[str, Any] = {"source_id": source_id, "units": "mJy", "wavelength_units": "um",
                           "kind": "multi-channel PSF photometry profile"}
    if by_visit:
        visits = {}
        for p in pts:
            visits.setdefault(p.get("epoch"), []).append(p)
        out["visits"] = [{"epoch": e, "mjd": float(np.mean([p["mjd"] for p in v])) if all(p.get("mjd") for p in v) else None,
                          "points": sorted(({"wavelength": p["wavelength"], "flux": p["flux"], "flux_err": p.get("flux_err"),
                                             "observation_id": p.get("observation_id")} for p in v),
                                           key=lambda q: q["wavelength"])}
                         for e, v in sorted(visits.items(), key=lambda kv: (kv[0] is None, kv[0]))]
    bins: dict[int, list[dict[str, Any]]] = {}
    for p in pts:
        ch = channel_for(p.get("detector"), p["wavelength"])
        if ch is not None:
            bins.setdefault(ch.index, []).append({**p, "_ch": ch})
    comb = []
    for idx in sorted(bins):
        v = bins[idx]
        ch = v[0]["_ch"]
        f = np.array([p["flux"] for p in v])
        e = np.array([p.get("flux_err") or np.nan for p in v])
        if np.all(np.isfinite(e)) and np.all(e > 0):
            w = 1 / e ** 2
            mean, err = float(np.sum(w * f) / w.sum()), float(1 / math.sqrt(w.sum()))
        else:
            mean, err = float(np.mean(f)), float(np.std(f) / math.sqrt(len(f))) if len(f) > 1 else None
        comb.append({"channel": idx, "label": ch.label, "wavelength": ch.wavelength_center,
                     "wavelength_min": ch.wavelength_min, "wavelength_max": ch.wavelength_max,
                     "flux": mean, "flux_err": err, "n": len(v),
                     "scatter": float(np.std(f)) if len(f) > 1 else None})
    out["combined"] = comb
    out["limitations"] = [
        "PSF-fitted flux assuming a Gaussian PSF: absolute level uncertain by ~10%, colours across one detector better.",
        "Points from different visits are combined in 'combined'; a variable source blurs it (see 'visits').",
        "Saturated or masked cores are excluded, so bright stars can have no spectrum at all.",
    ]
    return out


def compare_spectra(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Ratio of two combined spectra (two visits of one source, or two sources) on shared channels."""
    ia = {p["channel"]: p for p in a.get("combined", [])}
    ib = {p["channel"]: p for p in b.get("combined", [])}
    rows = []
    for ch in sorted(set(ia) & set(ib)):
        pa, pb = ia[ch], ib[ch]
        if not pb["flux"]:
            continue
        r = pa["flux"] / pb["flux"]
        ea = (pa["flux_err"] or 0) / pa["flux"] if pa["flux"] else 0
        eb = (pb["flux_err"] or 0) / pb["flux"]
        rows.append({"channel": ch, "wavelength": pa["wavelength"], "ratio": r, "ratio_err": abs(r) * math.hypot(ea, eb)})
    return {"a": a.get("source_id"), "b": b.get("source_id"), "n_channels": len(rows), "points": rows}
