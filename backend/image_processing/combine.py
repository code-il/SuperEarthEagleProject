"""Stacking, differencing and ratio images on a common grid.

All functions take plain arrays (NaN = invalid) plus optional variances,
so they can be used on any aligned data. They return new arrays and never
modify their inputs.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from scipy import ndimage

STACK_METHODS = ("mean", "median", "weighted")
# Variance of a median relative to the mean, for Gaussian noise (pi/2).
_MEDIAN_VARIANCE_FACTOR = np.pi / 2


@dataclass
class StackResult:
    data: np.ndarray
    variance: np.ndarray | None
    n_used: np.ndarray
    """Number of input frames contributing to each pixel."""
    method: str
    history: list[str] = field(default_factory=list)

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.data) & (self.n_used > 0)


@dataclass
class ComparisonResult:
    data: np.ndarray
    variance: np.ndarray | None
    significance: np.ndarray | None
    """data / sigma (difference) or (ratio - 1) / sigma (ratio)."""
    valid: np.ndarray
    mode: str
    history: list[str] = field(default_factory=list)


def _as_stack(images: Sequence[np.ndarray]) -> np.ndarray:
    if not images:
        raise ValueError("No images to combine.")
    shapes = {np.shape(i) for i in images}
    if len(shapes) != 1:
        raise ValueError(f"Images must share one grid; got shapes {sorted(shapes)}. Reproject first.")
    return np.stack([np.asarray(i, float) for i in images])


def stack_observations(images: Sequence[np.ndarray], method: str = "median",
                       variances: Sequence[np.ndarray] | None = None,
                       min_frames: int = 1) -> StackResult:
    """Combine aligned images pixel by pixel, ignoring NaNs.

    ``mean``     plain average; variance = sum(var) / n^2
    ``median``   robust to cosmic rays / movers; variance ~ (pi/2) * mean variance
    ``weighted`` inverse-variance weighted mean; variance = 1 / sum(1/var)

    Pixels with fewer than ``min_frames`` valid inputs are NaN.
    """
    if method not in STACK_METHODS:
        raise ValueError(f"method must be one of {STACK_METHODS}")
    cube = _as_stack(images)
    valid = np.isfinite(cube)
    vcube = None
    if variances is not None:
        vcube = _as_stack(variances)
        if vcube.shape != cube.shape:
            raise ValueError("variances must match images")
        valid &= np.isfinite(vcube) & (vcube > 0)
        vcube = np.where(valid, vcube, np.nan)
    cube = np.where(valid, cube, np.nan)
    n = valid.sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        if method == "weighted":
            if vcube is None:
                raise ValueError("weighted stacking needs variances")
            w = np.where(valid, 1.0 / vcube, 0.0)
            wsum = w.sum(axis=0)
            data = np.where(wsum > 0, np.nansum(np.where(valid, cube, 0) * w, axis=0) / wsum, np.nan)
            var = np.where(wsum > 0, 1.0 / wsum, np.nan)
        elif method == "mean":
            data = np.nanmean(cube, axis=0)
            var = np.nansum(vcube, axis=0) / n ** 2 if vcube is not None else None
        else:
            data = np.nanmedian(cube, axis=0)
            var = (_MEDIAN_VARIANCE_FACTOR * np.nansum(vcube, axis=0) / n ** 2) if vcube is not None else None
    low = n < max(1, min_frames)
    data = np.where(low, np.nan, data)
    if var is not None:
        var = np.where(low, np.nan, var)
    hist = [f"{method} stack of {len(images)} frames (min {min_frames} valid per pixel)"]
    return StackResult(data=data, variance=var, n_used=n.astype(np.int16), method=method, history=hist)


def subtract_images(image_a: np.ndarray, image_b: np.ndarray,
                    variance_a: np.ndarray | None = None, variance_b: np.ndarray | None = None,
                    match_background: bool = False) -> ComparisonResult:
    """Difference image A - B on a common grid.

    ``match_background`` removes the sigma-clipped median of the difference
    (a constant background offset between the two images, e.g. different
    zodiacal light). Positive residuals = brighter in A; negative = brighter
    in B. A source that moved leaves a +/- pair ("dipole"). Residuals can
    also come from PSF, background or calibration differences: they are
    not automatically astronomical changes.
    """
    a = np.asarray(image_a, float)
    b = np.asarray(image_b, float)
    if a.shape != b.shape:
        raise ValueError(f"Shapes differ: {a.shape} vs {b.shape}. Reproject onto one grid first.")
    valid = np.isfinite(a) & np.isfinite(b)
    diff = np.where(valid, a - b, np.nan)
    hist = ["difference A - B"]
    if match_background and valid.sum() >= 5:
        from astropy.stats import sigma_clipped_stats

        _, med, _ = sigma_clipped_stats(diff[valid], sigma=3, maxiters=10)
        diff = diff - med
        hist.append(f"removed background offset {med:.4f} between A and B")
    var = sig = None
    if variance_a is not None and variance_b is not None:
        var = np.asarray(variance_a, float) + np.asarray(variance_b, float)
        good = valid & np.isfinite(var) & (var > 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            sig = np.where(good, diff / np.sqrt(var), np.nan)
        var = np.where(good, var, np.nan)
    return ComparisonResult(diff, var, sig, valid, "difference", hist)


def divide_images(image_a: np.ndarray, image_b: np.ndarray,
                  variance_a: np.ndarray | None = None, variance_b: np.ndarray | None = None,
                  min_snr_b: float = 5.0, min_abs_b: float = 0.0) -> ComparisonResult:
    """Ratio image A / B.

    Division is only done where B is significantly non-zero: ``B / sigma_B
    >= min_snr_b`` when B's variance is known, else ``|B| > min_abs_b``.
    Other pixels are NaN. On background-subtracted data this means the
    ratio is only defined on sources, which is where it is meaningful (e.g.
    brightness change of a variable star). Variance by first-order error
    propagation: var(R) = R^2 (var_A/A^2 + var_B/B^2).
    """
    a = np.asarray(image_a, float)
    b = np.asarray(image_b, float)
    if a.shape != b.shape:
        raise ValueError(f"Shapes differ: {a.shape} vs {b.shape}. Reproject onto one grid first.")
    valid = np.isfinite(a) & np.isfinite(b) & (b != 0)
    hist = ["ratio A / B"]
    have_var = variance_a is not None and variance_b is not None
    if have_var:
        va = np.asarray(variance_a, float)
        vb = np.asarray(variance_b, float)
        with np.errstate(invalid="ignore", divide="ignore"):
            snr_b = np.abs(b) / np.sqrt(vb)
        valid &= np.isfinite(snr_b) & (snr_b >= min_snr_b)
        hist.append(f"B required to have S/N >= {min_snr_b}")
    else:
        valid &= np.abs(b) > min_abs_b
        hist.append(f"|B| required to exceed {min_abs_b}")
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(valid, a / b, np.nan)
        var = sig = None
        if have_var:
            var = np.where(valid, ratio ** 2 * (va / a ** 2 + vb / b ** 2), np.nan)
            var = np.where(np.isfinite(var), var, np.nan)
            sig = np.where(valid & np.isfinite(var) & (var > 0), (ratio - 1) / np.sqrt(var), np.nan)
    return ComparisonResult(ratio, var, sig, valid, "ratio", hist)


def gaussian_kernel_sigma_px(fwhm_from: float, fwhm_to: float, pixel_scale_arcsec: float) -> float:
    """Gaussian sigma (pixels) that degrades a PSF of ``fwhm_from`` to ``fwhm_to`` (arcsec)."""
    if fwhm_to <= fwhm_from:
        return 0.0
    return float(np.sqrt(fwhm_to ** 2 - fwhm_from ** 2) / 2.3548 / pixel_scale_arcsec)


def convolve_nan(image: np.ndarray, sigma_px: float, variance: np.ndarray | None = None):
    """Gaussian smoothing that ignores NaNs (normalised convolution)."""
    if sigma_px <= 0:
        return np.asarray(image, float), (None if variance is None else np.asarray(variance, float))
    img = np.asarray(image, float)
    valid = np.isfinite(img)
    num = ndimage.gaussian_filter(np.where(valid, img, 0.0), sigma_px, mode="constant")
    den = ndimage.gaussian_filter(valid.astype(float), sigma_px, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(valid & (den > 0.2), num / den, np.nan)
    var_out = None
    if variance is not None:
        # Approximate: smoothing a Gaussian of width s reduces white-noise variance by 4*pi*s^2
        v = np.asarray(variance, float)
        var_out = np.where(np.isfinite(out), v / max(1.0, 4 * np.pi * sigma_px ** 2), np.nan)
    return out, var_out


def psf_match(image_a, fwhm_a, image_b, fwhm_b, pixel_scale_arcsec,
              variance_a=None, variance_b=None):
    """Blur the sharper of two images so both have the same (Gaussian-approximated) PSF FWHM.

    Returns (a, var_a, b, var_b, history). SPHEREx PSFs are not Gaussian and
    vary across the detector, so this reduces, but does not remove,
    PSF-mismatch residuals in difference images.
    """
    hist = []
    if fwhm_a is None or fwhm_b is None or not np.isfinite(fwhm_a) or not np.isfinite(fwhm_b):
        return image_a, variance_a, image_b, variance_b, ["PSF matching skipped (PSF FWHM unknown)"]
    if fwhm_a < fwhm_b:
        s = gaussian_kernel_sigma_px(fwhm_a, fwhm_b, pixel_scale_arcsec)
        image_a, variance_a = convolve_nan(image_a, s, variance_a)
        hist.append(f"PSF-matched: blurred A ({fwhm_a:.2f}\") to {fwhm_b:.2f}\" (sigma {s:.2f} px)")
    elif fwhm_b < fwhm_a:
        s = gaussian_kernel_sigma_px(fwhm_b, fwhm_a, pixel_scale_arcsec)
        image_b, variance_b = convolve_nan(image_b, s, variance_b)
        hist.append(f"PSF-matched: blurred B ({fwhm_b:.2f}\") to {fwhm_a:.2f}\" (sigma {s:.2f} px)")
    else:
        hist.append("PSF FWHM already equal")
    return image_a, variance_a, image_b, variance_b, hist
