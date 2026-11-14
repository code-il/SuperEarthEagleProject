"""Source detection and photometry on processed SPHEREx frames (section A).

SPHEREx PSFs are undersampled (FWHM 7.5-9" on 6.15" pixels, about 1.3 px),
so the detector and photometry use a *pixel-integrated* circular Gaussian
PSF: each pixel's model value is the Gaussian integrated over the pixel,
not sampled at its centre. That keeps centroids unbiased by pixel phase.

* Detection: matched-filter signal-to-noise map (PSF-weighted, inverse
  variance, masked pixels get zero weight), peaks found with
  ``photutils.detection.find_peaks``.
* Position and flux: weighted least-squares PSF fit (x, y, flux, local
  background) on a 7x7 stamp; uncertainties from the fit covariance.
* Aperture photometry (``photutils.aperture``) is also reported, with a
  Gaussian aperture correction, as a model-independent cross-check.

Fluxes are converted from surface brightness (MJy/sr, the processed SCI
unit) to flux density in mJy using the pixel solid angle.
"""
from __future__ import annotations

import logging
import math
import warnings
from typing import Any

import numpy as np
from astropy.stats import sigma_clipped_stats
from astropy.wcs import WCS
from photutils.aperture import CircularAnnulus, CircularAperture, aperture_photometry
from photutils.detection import find_peaks
from scipy.ndimage import correlate
from scipy.optimize import least_squares
from scipy.special import erf

from backend.data_acquisition.fits_meta import quiet_wcs_logging

log = logging.getLogger(__name__)

DEFAULT_FWHM_ARCSEC = 8.0
"""Used when a frame carries no PSF_FWHM (SPHEREx: 7.5" at D1 to ~9" at D6)."""
DETECTION_NSIGMA = 5.0
STAMP_RADIUS_PX = 3
INTRINSIC_POS_FLOOR_PX = 0.05
"""Added in quadrature to fitted centroid errors (pixel-phase / PSF-shape systematics)."""
SAT_FLAG_NAMES = ("SATURATED", "OVERFLOW", "NONLINEAR", "BLOOM", "PERSIST")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def pixel_scale_arcsec(wcs: WCS) -> float:
    return float(np.sqrt(abs(np.linalg.det(wcs.celestial.pixel_scale_matrix))) * 3600)


def pixel_solid_angle_sr(wcs: WCS) -> float:
    return (pixel_scale_arcsec(wcs) / 206264.806) ** 2


def mjysr_to_mjy(wcs: WCS) -> float:
    """Multiply a summed MJy/sr pixel value by this to get mJy."""
    return pixel_solid_angle_sr(wcs) * 1e9


def ab_mag(flux_mjy: float | None, err_mjy: float | None = None) -> tuple[float | None, float | None]:
    """AB magnitude of a flux density in mJy (None if not positive)."""
    if flux_mjy is None or not np.isfinite(flux_mjy) or flux_mjy <= 0:
        return None, None
    m = -2.5 * math.log10(flux_mjy * 1e-3 / 3631.0)
    e = 2.5 / math.log(10) * err_mjy / flux_mjy if err_mjy else None
    return m, e


def _sigma_px(fwhm_arcsec: float | None, scale: float) -> float:
    return (fwhm_arcsec or DEFAULT_FWHM_ARCSEC) / 2.3548 / scale


def _pix_profile(c: np.ndarray, x0: float, s: float) -> np.ndarray:
    """Gaussian integrated over pixels centred at ``c`` (unit total)."""
    k = 1.0 / (math.sqrt(2) * s)
    return 0.5 * (erf((c + 0.5 - x0) * k) - erf((c - 0.5 - x0) * k))


def psf_model(xx: np.ndarray, yy: np.ndarray, x0: float, y0: float, s: float) -> np.ndarray:
    """Pixel-integrated circular Gaussian of unit total flux."""
    return _pix_profile(xx, x0, s) * _pix_profile(yy, y0, s)


def psf_kernel(s: float, radius: int = 2) -> np.ndarray:
    c = np.arange(-radius, radius + 1, dtype=float)
    k = np.outer(_pix_profile(c, 0.0, s), _pix_profile(c, 0.0, s))
    return k / k.sum()


def _frame_arrays(image: Any):
    """(data, variance, mask, flags, meta) from a ProcessedObservation or a bare array."""
    if hasattr(image, "data") and hasattr(image, "mask"):
        meta = {"observation_id": image.observation_id, "mjd": image.mjd,
                "observation_time": image.observation_time, "wavelength": image.wavelength,
                "detector": image.detector, "psf_fwhm_arcsec": image.psf_fwhm,
                "flag_definitions": getattr(image, "flag_definitions", {}) or {}}
        data = np.array(image.data, dtype=float)
        var = None if image.variance is None else np.array(image.variance, dtype=float)
        meta["raw"] = getattr(image, "raw", None)
        meta["wavelength_map"] = getattr(image, "wavelength_map", None)
        meta["data_release"] = getattr(image, "data_release", None)
        return data, var, np.asarray(image.mask), getattr(image, "flags", None), meta
    data = np.array(image, dtype=float)
    return data, None, (~np.isfinite(data)).astype(np.uint8), None, {}


SATURATED_POS_FLOOR_PX = 0.12
"""Per-axis error of a saturated-core RAW centroid (0.12 px = 0.74"; median offset 0.5-0.7" in validation)."""


def _raw_fallback(data: np.ndarray, var: np.ndarray, meta: dict[str, Any], mask: np.ndarray | None):
    """Background-subtracted RAW plane with inflated variance in masked pixels, or None.

    Saturated / overflowed cores are masked in SCI but RAW keeps the
    pipeline's best estimate. That is good enough for a centroid of the
    light distribution, not for photometry.
    """
    raw = meta.get("raw")
    if raw is None or mask is None:
        return None
    raw = np.array(raw, dtype=float)
    ok = (mask == 0) & np.isfinite(raw) & np.isfinite(data)
    if ok.sum() < 20:
        return None
    bkg = float(np.nanmedian((raw - data)[ok]))
    rb = raw - bkg
    v = np.where(np.isfinite(var), var, np.nanmedian(var))
    v = np.where(mask == 0, v, 4 * v + (0.1 * np.abs(rb)) ** 2)
    return rb, v


def _fit_with_raw_fallback(data, v, meta, mask, x0, y0, s, saturated: bool = False):
    """PSF fit on SCI; for saturated or masked cores use a RAW centroid. Returns (fit, used_raw).

    ``saturated``: a saturation-type flag lies within 2 px. Then even a
    "successful" SCI fit is biased (the clipped core is partly filled by
    unsaturated frames in a stack, or missing), so RAW is always used.
    """
    fit = fit_psf(data, v, x0, y0, s)
    if not saturated and fit is not None and fit["psf_coverage"] >= 0.7:
        return fit, False
    fb = _raw_fallback(data, v, meta, mask)
    if fb is None:
        return fit, False
    from backend.image_processing.align import centroid
    cx, cy = x0, y0
    for _ in range(5):
        c = centroid(fb[0], cx, cy, 2.5)
        if c is None:
            return fit, False
        cx, cy = c
    if math.hypot(cx - x0, cy - y0) > 2.0:
        return fit, False
    # Intensity-weighted centroid of RAW: on the five targets it lands 0.5-0.7" (median)
    # from the catalogue position for saturated stars, vs ~1-2" for a PSF fit to the
    # clipped core. Flux is not measured.
    e = SATURATED_POS_FLOOR_PX
    return {"x": cx, "y": cy, "x_err": e, "y_err": e, "flux": float("nan"), "flux_err": float("nan"),
            "background": 0.0, "chi2_red": float("nan"), "npix": 0, "psf_coverage": 0.0}, True


def wavelength_at(meta: dict[str, Any], x: float, y: float) -> float | None:
    """Wavelength (um) at pixel (x, y): the linear variable filter changes wavelength
    across a frame (~0.01 um over 3', a quarter of a channel), so the frame-centre
    value is not the source's wavelength unless the source is at the centre."""
    wm = meta.get("wavelength_map")
    if wm is not None:
        iy, ix = int(round(y)), int(round(x))
        if 0 <= iy < wm.shape[0] and 0 <= ix < wm.shape[1] and np.isfinite(wm[iy, ix]):
            return float(wm[iy, ix])
    return meta.get("wavelength")


def _sat_bits(flag_definitions: dict[str, Any]) -> int:
    bits = 0
    for name, v in (flag_definitions or {}).items():
        if any(s in str(name).upper() for s in SAT_FLAG_NAMES):
            try:
                bits |= 1 << int(v["bit"] if isinstance(v, dict) else v)
            except (TypeError, ValueError, KeyError):
                pass
    return bits


def _noise_variance(data: np.ndarray, var: np.ndarray | None) -> np.ndarray:
    """Per-pixel variance; estimated from robust image statistics when absent."""
    if var is not None and np.isfinite(var).any():
        v = np.where(np.isfinite(var) & (var > 0), var, np.nan)
        return v
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, _, std = sigma_clipped_stats(data[np.isfinite(data)], sigma=3.0)
    return np.full(data.shape, max(float(std), 1e-12) ** 2)


def matched_filter_snr(data: np.ndarray, var: np.ndarray, sigma_px: float) -> np.ndarray:
    """S/N of a point source centred on each pixel (PSF-weighted, inverse-variance)."""
    w = np.where(np.isfinite(data) & np.isfinite(var), 1.0 / var, 0.0)
    d = np.where(w > 0, data, 0.0)
    k = psf_kernel(sigma_px)
    num = correlate(d * w, k, mode="constant")
    den = correlate(w, k * k, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        snr = num / np.sqrt(den)
    # Require most of the PSF footprint to be valid, otherwise edges and holes make peaks.
    cover = correlate((w > 0).astype(float), k, mode="constant")
    snr[cover < 0.6] = np.nan
    return snr


# --------------------------------------------------------------------------
# PSF fitting
# --------------------------------------------------------------------------
def fit_psf(data: np.ndarray, var: np.ndarray, x0: float, y0: float, sigma_px: float,
            fixed_position: bool = False, radius: int = STAMP_RADIUS_PX) -> dict[str, Any] | None:
    """Weighted least-squares fit of flux (+ position unless fixed) and local background.

    Returns pixel-unit results (flux = summed surface brightness) or None if
    the stamp has too few valid pixels.
    """
    ny, nx = data.shape
    ix, iy = int(round(x0)), int(round(y0))
    x1, x2 = max(ix - radius, 0), min(ix + radius + 1, nx)
    y1, y2 = max(iy - radius, 0), min(iy + radius + 1, ny)
    yy, xx = np.mgrid[y1:y2, x1:x2]
    d = data[y1:y2, x1:x2]
    v = var[y1:y2, x1:x2]
    ok = np.isfinite(d) & np.isfinite(v) & (v > 0)
    if ok.sum() < 6:
        return None
    xx, yy, d, sig = xx[ok].astype(float), yy[ok].astype(float), d[ok], np.sqrt(v[ok])
    # how much of the PSF the valid pixels cover (masked core => low)
    core = psf_model(xx, yy, x0, y0, sigma_px).sum()

    if fixed_position:
        a = np.vstack([psf_model(xx, yy, x0, y0, sigma_px), np.ones_like(d)]).T / sig[:, None]
        b = d / sig
        coef, *_ = np.linalg.lstsq(a, b, rcond=None)
        res = b - a @ coef
        dof = max(len(d) - 2, 1)
        try:
            cov = np.linalg.inv(a.T @ a)
        except np.linalg.LinAlgError:
            return None
        return {"x": x0, "y": y0, "x_err": 0.0, "y_err": 0.0, "flux": float(coef[0]),
                "flux_err": float(math.sqrt(cov[0, 0])), "background": float(coef[1]),
                "chi2_red": float((res ** 2).sum() / dof), "npix": int(len(d)), "psf_coverage": float(core)}

    def resid(p):
        return (p[2] * psf_model(xx, yy, p[0], p[1], sigma_px) + p[3] - d) / sig

    f0 = max(float(np.nansum(d - np.nanmedian(d))), 1e-6)
    p0 = [x0, y0, f0, float(np.nanmedian(d)) * 0]
    try:
        r = least_squares(resid, p0, method="lm", max_nfev=200)
    except Exception:  # pragma: no cover - numerical failure
        return None
    if not r.success or not np.all(np.isfinite(r.x)):
        return None
    dof = max(len(d) - 4, 1)
    chi2r = float((r.fun ** 2).sum() / dof)
    try:
        cov = np.linalg.inv(r.jac.T @ r.jac)
    except np.linalg.LinAlgError:
        return None
    if np.any(np.diag(cov) < 0):
        return None
    infl = max(1.0, chi2r)  # don't let a poor fit claim formal precision
    e = np.sqrt(np.diag(cov) * infl)
    x, y = float(r.x[0]), float(r.x[1])
    if abs(x - x0) > 1.5 or abs(y - y0) > 1.5:  # wandered off the peak
        return None
    return {"x": x, "y": y, "x_err": float(math.hypot(e[0], INTRINSIC_POS_FLOOR_PX)),
            "y_err": float(math.hypot(e[1], INTRINSIC_POS_FLOOR_PX)),
            "flux": float(r.x[2]), "flux_err": float(e[2]), "background": float(r.x[3]),
            "chi2_red": chi2r, "npix": int(len(d)), "psf_coverage": float(core)}


# --------------------------------------------------------------------------
# A. detection
# --------------------------------------------------------------------------
def detect_sources(image: Any, wcs: WCS | None = None, *, nsigma: float = DETECTION_NSIGMA,
                   fwhm_arcsec: float | None = None, max_sources: int = 200) -> list[dict[str, Any]]:
    """Detect point sources in one processed frame.

    ``image`` is a ``ProcessedObservation`` (uses its variance, mask, PSF
    FWHM and time) or a 2-D array (noise estimated from the image).
    Returns one record per source with pixel and sky position, positional
    uncertainty, PSF-fit flux (mJy), S/N, detection significance and
    quality flags. Positions include no registration error yet; see
    ``registration.estimate_registration``.
    """
    data, var, mask, flags, meta = _frame_arrays(image)
    wcs = wcs or getattr(image, "wcs", None)
    if wcs is None:
        raise ValueError("a WCS is required")
    scale = pixel_scale_arcsec(wcs)
    fwhm = fwhm_arcsec or meta.get("psf_fwhm_arcsec") or DEFAULT_FWHM_ARCSEC
    s = _sigma_px(fwhm, scale)
    data = np.where(mask == 0, data, np.nan) if mask is not None else data
    v = _noise_variance(data, var)
    sat_bits = _sat_bits(meta.get("flag_definitions", {}))
    # Saturated cores are masked in SCI, which would turn a bright star into a
    # ring of peaks. For the detection map only, fill masked pixels next to a
    # saturation flag with RAW (inflated variance). Other masked pixels (hot,
    # cosmic rays, artefacts) stay empty so they cannot create sources.
    det_data, det_var = data, v
    fb = _raw_fallback(data, v, meta, mask)
    if fb is not None and flags is not None and sat_bits:
        from scipy.ndimage import binary_dilation
        sat = (np.asarray(flags).astype(np.int64) & sat_bits) != 0
        fill = (mask != 0) & binary_dilation(sat, iterations=2) & np.isfinite(fb[0])
        if fill.any():
            det_data = np.where(fill, fb[0], data)
            det_var = np.where(fill, fb[1], v)
    snr = matched_filter_snr(det_data, det_var, s)

    # Empirical noise check: if the variance plane underestimates the noise,
    # the S/N map's robust spread exceeds 1; rescale (never boost) S/N.
    # Pixels near bright sources (PSF wings, saturation halos) are excluded
    # from this estimate, else one bright star would suppress every detection.
    from scipy.ndimage import binary_dilation
    s0 = np.nan_to_num(snr, nan=0.0)
    near = binary_dilation(s0 > 10, iterations=3) | binary_dilation(s0 > 50, iterations=6)
    finite = snr[np.isfinite(snr) & ~near]
    noise_scale = 1.0
    if finite.size > 50:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _, med, std = sigma_clipped_stats(finite, sigma=3.0)
        noise_scale = max(1.0, float(std))
        snr = (snr - med) / noise_scale
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # NoDetectionsWarning on empty frames
        peaks = find_peaks(np.nan_to_num(snr, nan=-np.inf), threshold=nsigma, box_size=3, border_width=1)
    if peaks is None or len(peaks) == 0:
        return []
    peaks.sort("peak_value", reverse=True)
    peaks = peaks[:max_sources]

    conv = mjysr_to_mjy(wcs)
    ny, nx = data.shape
    out: list[dict[str, Any]] = []
    pxy = np.array([[p["x_peak"], p["y_peak"]] for p in peaks], dtype=float)
    for k, p in enumerate(peaks):
        x0, y0 = float(p["x_peak"]), float(p["y_peak"])
        sat_here = bool(flags is not None and sat_bits and _flag_within(flags, x0, y0, 2.0, sat_bits)
                        and _masked_within(mask, x0, y0, 1.6))
        fit, used_raw = _fit_with_raw_fallback(data, v * noise_scale ** 2, meta, mask, x0, y0, s, sat_here)
        q: list[str] = ["saturated_core_raw_fit"] if used_raw else []
        if fit is None:
            fit = {"x": x0, "y": y0, "x_err": 0.5, "y_err": 0.5, "flux": float("nan"), "flux_err": float("nan"),
                   "background": 0.0, "chi2_red": float("nan"), "npix": 0, "psf_coverage": 0.0}
            q.append("fit_failed")
        x, y = fit["x"], fit["y"]
        with quiet_wcs_logging():
            ra, dec = wcs.pixel_to_world_values(x, y)
        r_mask = _masked_within(mask, x, y, 1.6)
        if r_mask:
            q.append("masked_core")
        if flags is not None and sat_bits and _flag_within(flags, x, y, 2.0, sat_bits):
            q.append("saturation_nearby")
        if min(x, y, nx - 1 - x, ny - 1 - y) < 2:
            q.append("edge")
        d_others = np.hypot(*(pxy - [x0, y0]).T)
        d_others[k] = np.inf
        if d_others.min() < 2.5:
            q.append("blended")
        if np.isfinite(fit["chi2_red"]) and fit["chi2_red"] > 5:
            q.append("poor_psf_fit")
        flux = fit["flux"] * conv
        ferr = fit["flux_err"] * conv
        snr_fit = flux / ferr if ferr and np.isfinite(ferr) and ferr > 0 else float("nan")
        if used_raw or "masked_core" in q:
            flux = ferr = snr_fit = float("nan")  # saturated: flux not trustworthy
        quality = max(0.0, 1.0 - 0.25 * len([f for f in q if f != "blended"]) - 0.1 * ("blended" in q))
        out.append({
            "id": f"{meta.get('observation_id', 'frame')}:{k}",
            "observation_id": meta.get("observation_id"), "mjd": meta.get("mjd"),
            "observation_time": meta.get("observation_time"), "wavelength": wavelength_at(meta, x, y),
            "frame_wavelength": meta.get("wavelength"), "detector": meta.get("detector"),
            "data_release": meta.get("data_release"), "x": x, "y": y, "x_err": fit["x_err"], "y_err": fit["y_err"],
            "ra": float(ra) % 360.0, "dec": float(dec),
            "ra_err_arcsec": fit["x_err"] * scale, "dec_err_arcsec": fit["y_err"] * scale,
            "flux_mjy": _num(flux), "flux_err_mjy": _num(ferr), "snr": _num(snr_fit),
            "significance": float(p["peak_value"]), "psf_chi2_red": _num(fit["chi2_red"]),
            "psf_coverage": round(fit["psf_coverage"], 3), "noise_scale": round(noise_scale, 3),
            "quality_flags": q, "quality": round(quality, 2),
        })
    return out


def _num(v: float) -> float | None:
    return float(v) if v is not None and np.isfinite(v) else None


def _masked_within(mask: np.ndarray | None, x: float, y: float, r: float) -> bool:
    if mask is None:
        return False
    yy, xx = np.indices(mask.shape)
    sel = np.hypot(xx - x, yy - y) <= r
    return bool((mask[sel] != 0).any())


def _flag_within(flags: np.ndarray, x: float, y: float, r: float, bits: int) -> bool:
    yy, xx = np.indices(flags.shape)
    sel = np.hypot(xx - x, yy - y) <= r
    return bool((np.asarray(flags[sel]).astype(np.int64) & bits).any())


# --------------------------------------------------------------------------
# Photometry at given positions
# --------------------------------------------------------------------------
def extract_source_photometry(image: Any, sources: list[dict[str, Any]] | list[tuple[float, float]], *,
                              wcs: WCS | None = None, aperture_fwhm: float = 1.0,
                              fwhm_arcsec: float | None = None) -> list[dict[str, Any]]:
    """Forced photometry at fixed sky positions in one frame.

    ``sources`` are dicts with ``ra``/``dec`` (or (ra, dec) tuples). For each
    one: PSF-fit flux at the fixed position (best S/N for undersampled
    data), and aperture flux (radius = ``aperture_fwhm`` x FWHM, local
    annulus background, Gaussian aperture correction). Fluxes in mJy.
    A source off the frame, or whose PSF footprint is mostly masked, gets
    ``flux_mjy=None`` and a quality flag, never a fake zero.
    """
    data, var, mask, flags, meta = _frame_arrays(image)
    wcs = wcs or getattr(image, "wcs", None)
    scale = pixel_scale_arcsec(wcs)
    fwhm = fwhm_arcsec or meta.get("psf_fwhm_arcsec") or DEFAULT_FWHM_ARCSEC
    s = _sigma_px(fwhm, scale)
    data = np.where(mask == 0, data, np.nan)
    v = _noise_variance(data, var)
    conv = mjysr_to_mjy(wcs)
    sat_bits = _sat_bits(meta.get("flag_definitions", {}))
    ny, nx = data.shape
    r_ap = max(aperture_fwhm * fwhm / scale, 1.0)
    ap_corr = 1.0 / (1.0 - math.exp(-r_ap ** 2 / (2 * s * s)))
    out = []
    for src in sources:
        ra, dec = (src["ra"], src["dec"]) if isinstance(src, dict) else src
        with quiet_wcs_logging():
            x, y = (float(t) for t in wcs.world_to_pixel_values(ra, dec))
        rec: dict[str, Any] = {"ra": ra, "dec": dec, "x": x, "y": y,
                               "observation_id": meta.get("observation_id"), "mjd": meta.get("mjd"),
                               "observation_time": meta.get("observation_time"),
                               "wavelength": wavelength_at(meta, x, y), "frame_wavelength": meta.get("wavelength"),
                               "detector": meta.get("detector"), "data_release": meta.get("data_release"),
                               "flux_mjy": None, "flux_err_mjy": None, "snr": None,
                               "aperture_flux_mjy": None, "aperture_flux_err_mjy": None, "quality_flags": []}
        if isinstance(src, dict) and "id" in src:
            rec["source_id"] = src["id"]
        if not (0.5 <= x <= nx - 1.5 and 0.5 <= y <= ny - 1.5):
            rec["quality_flags"].append("off_frame")
            out.append(rec)
            continue
        fit = fit_psf(data, v, x, y, s, fixed_position=True)
        if fit is None or fit["psf_coverage"] < 0.5:
            rec["quality_flags"].append("masked_core" if _masked_within(mask, x, y, 1.6) else "insufficient_pixels")
        else:
            rec["flux_mjy"] = fit["flux"] * conv
            rec["flux_err_mjy"] = fit["flux_err"] * conv * max(1.0, math.sqrt(fit["chi2_red"]))
            rec["snr"] = rec["flux_mjy"] / rec["flux_err_mjy"] if rec["flux_err_mjy"] else None
            rec["psf_coverage"] = round(fit["psf_coverage"], 3)
            if fit["psf_coverage"] < 0.85:
                rec["quality_flags"].append("partially_masked")
        if _masked_within(mask, x, y, 1.6) and "masked_core" not in rec["quality_flags"]:
            rec["quality_flags"].append("masked_core")
        if flags is not None and sat_bits and _flag_within(flags, x, y, 2.0, sat_bits):
            rec["quality_flags"].append("saturation_nearby")
        # aperture photometry (model-independent check)
        try:
            ap = CircularAperture((x, y), r=r_ap)
            an = CircularAnnulus((x, y), r_in=max(r_ap + 1.0, 2.5), r_out=max(r_ap + 3.5, 5.0))
            good = np.isfinite(data)
            t = aperture_photometry(np.nan_to_num(data), ap, error=np.sqrt(np.nan_to_num(v, nan=0.0)),
                                    mask=~good)
            amask = an.to_mask(method="center").multiply(np.where(good, data, np.nan))
            bkg = float(np.nanmedian(amask[amask != 0])) if amask is not None and np.isfinite(amask).any() else 0.0
            if not np.isfinite(bkg):
                bkg = 0.0
            ap_valid = ap.to_mask(method="exact").multiply(good.astype(float))
            area_valid = float(np.nansum(ap_valid)) if ap_valid is not None else 0.0
            if area_valid >= 0.9 * ap.area:
                f = (float(t["aperture_sum"][0]) - bkg * ap.area) * conv * ap_corr
                fe = float(t["aperture_sum_err"][0]) * conv * ap_corr
                rec["aperture_flux_mjy"], rec["aperture_flux_err_mjy"] = f, fe
        except Exception as exc:  # pragma: no cover - aperture edge cases
            log.debug("aperture photometry failed: %s", exc)
        out.append(rec)
    return out
