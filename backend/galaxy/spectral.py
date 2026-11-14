"""SPHEREx DR1+QR3 spectrum of one catalogued object, and a class read from that spectrum.

``measure_spectrum`` searches every SPHEREx image (all six detectors, DR1+QR3) covering the
object, processes a small field around it (``process_series``), does forced PSF photometry
at the object's proper-motion-propagated position in every frame, and bins the points on the
102 SPHEREx channels (``generate_spectrum``). Everything reuses the analysis pipeline.

``classify_spectrum`` reads a class from the 0.75–5 µm spectral energy distribution:

* a blackbody fit (F_nu) gives a colour temperature, mapped to O/B…M / brown dwarf;
* the 3.3 µm methane band (F[3.20–3.40] / F[3.85–4.05]) and the 1.4 µm water band
  (F[1.33–1.45] / F[1.20–1.30]) flag the molecular absorption of cool brown dwarfs;
* a 4–5 µm excess above the blackbody flags warm dust (young star with a disk, or an AGB envelope).

These are photometric indices on PSF-fitted fluxes, not a calibrated spectral type: each
result lists its evidence, and ``confidence`` falls with low S/N, a poor fit, or a colour
temperature above ~6000 K (the 0.75–5 µm range is on the Rayleigh–Jeans tail of hot stars).
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from backend.analysis.photometry import forced_photometry, generate_light_curve, generate_spectrum
from backend.data_acquisition.archive import search_observations
from backend.image_processing.pipeline import assign_epochs, process_series

from .classes import CLASS_ID, CLASSES

GAIA_REF_MJD = 57388.5  # J2016.0
H, C, K = 6.62607015e-34, 2.99792458e8, 1.380649e-23


def measure_spectrum(ra: float, dec: float, *, pmra: float | None = None, pmdec: float | None = None,
                     ref_mjd: float = GAIA_REF_MJD, fov: float = 0.03, name: str | None = None,
                     max_observations: int | None = 40) -> dict[str, Any]:
    t0 = time.time()
    # One archive search (DR1+QR3, all detectors), retried on IRSA's transient 5xx errors ...
    for attempt in range(4):
        try:
            records = search_observations(ra, dec, fov)
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 3 or not any(code in str(exc) for code in ("502", "503", "504", "Proxy", "timed out")):
                raise
            time.sleep(20 * (attempt + 1))
    # ... then one detector at a time, which keeps each cutout request under the per-request cap.
    obs, n_err = [], 0
    for det in (1, 2, 3, 4, 5, 6):
        series = process_series(ra, dec, fov, records=records, detectors=(det,), make_gifs=False,
                                max_observations=max_observations)
        obs += series.observations
        n_err += len(series.errors)
    src = {"source_id": name or "target", "ra": ra, "dec": dec}
    if pmra is not None and pmdec is not None and math.isfinite(pmra) and math.isfinite(pmdec):
        src["angular_motion"] = {"ra_at_ref": ra, "dec_at_ref": dec, "ref_mjd": ref_mjd,
                                 "pm_ra_cosdec_mas_yr": pmra, "pm_dec_mas_yr": pmdec}
    epochs = assign_epochs(obs)
    phot = forced_photometry(src, obs, epochs)
    lc = generate_light_curve(src["source_id"], obs, source=src, epochs=epochs, photometry=phot)
    spec = generate_spectrum(src["source_id"], lc["points"], by_visit=False)
    rel = sorted({o.data_release for o in obs if getattr(o, "data_release", None)})
    out = {"name": name, "ra": ra, "dec": dec, "pmra": pmra, "pmdec": pmdec, "fov_deg": fov,
           "n_frames": len(obs), "n_good": lc["n_good"], "rejected_flags": lc["rejected_flags"],
           "releases": rel, "detectors": sorted({o.detector for o in obs if o.detector}),
           "n_epochs": len(set(epochs.values())),
           "mjd_range": [min((o.mjd for o in obs if o.mjd), default=None), max((o.mjd for o in obs if o.mjd), default=None)],
           "spectrum": [{"w": round(p["wavelength"], 4), "f": p["flux"], "e": p["flux_err"], "n": p["n"]}
                        for p in spec["combined"]],
           "errors": n_err, "runtime_s": round(time.time() - t0, 1)}
    if len(out["spectrum"]) < 8:
        sat = lc["rejected_flags"].get("saturation_nearby", 0) + lc["rejected_flags"].get("masked_core", 0)
        out["status"] = "saturated" if sat > lc["n_frames"] / 3 else ("no_data" if not obs else "too_faint")
        out["classification"] = None
    else:
        out["status"] = "ok"
        out["classification"] = classify_spectrum(out["spectrum"])
    return out


def _bnu(w_um: np.ndarray, T: float) -> np.ndarray:
    lam = w_um * 1e-6
    nu = C / lam
    x = np.clip(H * nu / (K * T), 1e-8, 700)
    return nu ** 3 / np.expm1(x)


def _band(w, f, e, lo, hi):
    s = (w >= lo) & (w <= hi) & np.isfinite(f)
    if s.sum() < 2:
        return None, None
    ww = 1 / np.where(np.isfinite(e[s]) & (e[s] > 0), e[s], np.nanmedian(np.abs(f[s])) + 1e-9) ** 2
    m = float(np.sum(ww * f[s]) / ww.sum())
    return m, float(1 / math.sqrt(ww.sum()))


def _ratio(w, f, e, a, b):
    fa, ea = _band(w, f, e, *a)
    fb, eb = _band(w, f, e, *b)
    if fa is None or fb is None or fb <= 0:
        return None, None
    r = fa / fb
    return r, abs(r) * math.hypot((ea or 0) / max(abs(fa), 1e-12), (eb or 0) / fb)


def classify_spectrum(spectrum: list[dict[str, Any]]) -> dict[str, Any]:
    w = np.array([p["w"] for p in spectrum], float)
    f = np.array([p["f"] for p in spectrum], float)
    e = np.array([p["e"] if p["e"] else np.nan for p in spectrum], float)
    e = np.where(np.isfinite(e) & (e > 0), e, np.nanmedian(np.abs(f)) * 0.1 + 1e-9)
    e = np.sqrt(e ** 2 + (0.03 * f) ** 2)  # 3 % calibration floor
    snr = float(np.nanmedian(f / e))
    good = np.isfinite(f) & (f / e > 2)
    evidence: list[str] = []
    if good.sum() < 6:
        return {"class_id": None, "label": "too faint", "confidence": 0.0, "snr_median": round(snr, 1),
                "evidence": ["fewer than six channels at S/N > 2"]}
    # Blackbody fit on the continuum (exclude the main molecular bands).
    cont = good & ~(((w > 3.15) & (w < 3.45)) | ((w > 1.32) & (w < 1.5)) | ((w > 1.75) & (w < 2.05)) |
                    ((w > 4.2) & (w < 4.4)) | ((w > 4.5) & (w < 4.8)))
    if cont.sum() < 6:
        cont = good
    best = (np.inf, None, None)
    for T in np.geomspace(150, 40000, 220):
        b = _bnu(w[cont], T)
        wt = 1 / e[cont] ** 2
        a = np.sum(wt * f[cont] * b) / np.sum(wt * b * b)
        chi2 = float(np.sum(wt * (f[cont] - a * b) ** 2))
        if chi2 < best[0]:
            best = (chi2, T, a)
    chi2, T, a = best
    dof = max(int(cont.sum()) - 2, 1)
    chi2r = chi2 / dof
    model = a * _bnu(w, T)
    evidence.append(f"blackbody colour temperature ≈ {T:,.0f} K (χ²/dof = {chi2r:.1f}, {int(cont.sum())} channels)")

    ch4, ch4e = _ratio(w, f, e, (3.20, 3.40), (3.85, 4.05))
    h2o, h2oe = _ratio(w, f, e, (1.33, 1.45), (1.20, 1.30))
    red, _ = _ratio(w, f, e, (4.4, 5.0), (1.0, 1.3))
    exc_sel = good & (w > 4.0)
    excess = None
    if exc_sel.sum() >= 3:
        resid = (f[exc_sel] - model[exc_sel]) / e[exc_sel]
        excess = float(np.median(resid)), float(np.median(f[exc_sel] / np.maximum(model[exc_sel], 1e-12)))

    if T > 10000:
        cid = CLASS_ID["ob"]
    elif T > 7500:
        cid = CLASS_ID["a"]
    elif T > 6000:
        cid = CLASS_ID["f"]
    elif T > 5200:
        cid = CLASS_ID["g"]
    elif T > 3700:
        cid = CLASS_ID["k"]
    elif T > 2400:
        cid = CLASS_ID["m"]
    else:
        cid = CLASS_ID["bd"]
    features: list[str] = []
    if ch4 is not None and ch4e is not None and ch4 < 0.6 and (0.6 - ch4) > 2 * ch4e:
        features.append("CH4 3.3 µm")
        evidence.append(f"methane band: F(3.3)/F(3.95) = {ch4:.2f} ± {ch4e:.2f} (< 0.6 → T/Y dwarf)")
        cid = CLASS_ID["bd"]
    if h2o is not None and h2oe is not None and h2o < 0.7 and (0.7 - h2o) > 2 * h2oe:
        features.append("H2O 1.4 µm")
        evidence.append(f"water band: F(1.4)/F(1.25) = {h2o:.2f} ± {h2oe:.2f}")
        if cid == CLASS_ID["m"] and T < 2900:
            cid = CLASS_ID["bd"]
    if red is not None and red > 3:
        features.append("very red 1–5 µm")
        evidence.append(f"F(4.4–5)/F(1–1.3) = {red:.1f}: most light emerges beyond 4 µm (very cold)")
        if T < 1500:
            cid = CLASS_ID["bd"]
    if excess and excess[0] > 3 and excess[1] > 1.3 and T > 2400:
        features.append("4–5 µm excess")
        evidence.append(f"4–5 µm flux {excess[1]:.1f}× the blackbody: warm dust (disk or envelope)")

    conf = 0.9
    conf *= min(1.0, max(0.2, (snr - 2) / 18))
    conf *= 1.0 if chi2r < 5 else 0.75 if chi2r < 25 else 0.5
    if cid <= CLASS_ID["f"]:
        conf *= 0.6
        evidence.append("hot star: 0.75–5 µm is the Rayleigh–Jeans tail, so the temperature is only a lower bound")
    if features and cid == CLASS_ID["bd"]:
        conf = min(0.95, conf + 0.15)
    label = next(c["label"] for c in CLASSES if c["id"] == cid)
    return {"class_id": cid, "label": label, "confidence": round(float(conf), 2), "t_color_k": round(float(T)),
            "chi2_red": round(chi2r, 2), "snr_median": round(snr, 1), "features": features,
            "indices": {"ch4_3p3": ch4 and round(ch4, 3), "h2o_1p4": h2o and round(h2o, 3), "red_4p7_1p15": red and round(red, 2),
                        "excess_4_5um": excess and round(excess[1], 2)},
            "bb_model": [round(float(x), 6) for x in model], "evidence": evidence,
            "method": "blackbody + molecular-band indices on SPHEREx DR1+QR3 forced photometry"}
