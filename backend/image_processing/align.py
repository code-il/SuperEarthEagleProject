"""Placing observations on a common celestial grid.

SPHEREx observes the same patch with very different detector orientations
(cutouts of one target are rotated by anything from -160 to +150 deg between
epochs), so frames must be resampled before they can be compared pixel by
pixel. The default target grid is a north-up, east-left TAN projection
centred exactly on the requested position at the native 6.15"/px scale.

Reprojection uses the files' own WCS (including the SIP distortion terms).
It does not correct errors in that WCS: if a frame's astrometry is off,
the aligned frame is off by the same amount. ``check_alignment`` measures
residual offsets from stars so this can be detected.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from astropy.wcs import WCS, FITSFixedWarning

from backend.data_acquisition.fits_meta import quiet_wcs_logging

NATIVE_PIXEL_SCALE_ARCSEC = 6.15
REPROJECT_METHODS = ("interp", "exact", "nearest")


def make_reference_wcs(ra: float, dec: float, fov_deg: float,
                       pixel_scale_arcsec: float = NATIVE_PIXEL_SCALE_ARCSEC) -> tuple[WCS, tuple[int, int]]:
    """North-up TAN grid covering ``fov_deg`` with the target on the central pixel.

    The pixel count is odd so (ra, dec) falls exactly on a pixel centre.
    """
    if pixel_scale_arcsec <= 0:
        raise ValueError("pixel_scale_arcsec must be positive")
    n = max(3, int(math.ceil(fov_deg * 3600.0 / pixel_scale_arcsec)))
    if n % 2 == 0:
        n += 1
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2.0, (n + 1) / 2.0]
    w.wcs.cdelt = [-pixel_scale_arcsec / 3600.0, pixel_scale_arcsec / 3600.0]
    w.wcs.cunit = ["deg", "deg"]
    w.wcs.radesys = "ICRS"
    w.wcs.set()
    return w, (n, n)


def cutout_size_for_grid(fov_deg: float, pixel_scale_arcsec: float = NATIVE_PIXEL_SCALE_ARCSEC) -> float:
    """Cutout width (deg) needed so an arbitrarily rotated cutout covers the whole grid."""
    return fov_deg * math.sqrt(2.0) + 4 * pixel_scale_arcsec / 3600.0


def select_reference(valid_fractions: Sequence[float], method: str | int = "best") -> int:
    """Index of the reference observation: ``"first"``, ``"best"`` (most valid pixels) or an index."""
    if not valid_fractions:
        raise ValueError("No observations to choose a reference from.")
    if isinstance(method, int):
        if not 0 <= method < len(valid_fractions):
            raise ValueError("Reference index out of range.")
        return method
    if method == "first":
        return 0
    if method == "best":
        return int(np.argmax(valid_fractions))
    raise ValueError("reference must be 'first', 'best' or an integer index")


def reproject_observation(image: np.ndarray, input_wcs: WCS, reference_wcs: WCS,
                          shape_out: tuple[int, int] | None = None,
                          method: str = "interp") -> tuple[np.ndarray, np.ndarray]:
    """Resample ``image`` onto ``reference_wcs``. Returns (array, footprint).

    ``interp`` = bilinear (default; preserves surface brightness, smooths
    noise slightly); ``exact`` = flux-conserving spherical-polygon overlap
    (slower); ``nearest`` = nearest neighbour (for bitmasks: no blending).
    NaN input pixels propagate to the output pixels they touch.
    """
    from reproject import reproject_exact, reproject_interp

    if method not in REPROJECT_METHODS:
        raise ValueError(f"method must be one of {REPROJECT_METHODS}")
    if shape_out is None:
        shape_out = tuple(int(s) for s in reference_wcs.array_shape)  # type: ignore[assignment]
    with quiet_wcs_logging(), warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        warnings.simplefilter("ignore", RuntimeWarning)
        if method == "exact":
            arr, fp = reproject_exact((image, input_wcs), reference_wcs, shape_out=shape_out)
        else:
            order = "bilinear" if method == "interp" else "nearest-neighbor"
            arr, fp = reproject_interp((image, input_wcs), reference_wcs, shape_out=shape_out, order=order)
    return np.asarray(arr, float), np.asarray(fp, float)


def reproject_bitmask(bits: np.ndarray, input_wcs: WCS, reference_wcs: WCS,
                      shape_out: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resampling of an integer bitmask (bits are never blended)."""
    arr, fp = reproject_observation(bits.astype(np.float64), input_wcs, reference_wcs, shape_out, "nearest")
    out = np.where(np.isfinite(arr) & (fp > 0), arr, 0).astype(np.int64)
    return out


def bilinear_contributors(input_wcs: WCS, reference_wcs: WCS, shape_out: tuple[int, int],
                          shape_in: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """The four input pixels behind every output pixel of a bilinear resampling, and their weights.

    Returns ``(idx, w)``, each of shape ``(4, ny*nx)``: flat input-pixel
    indices (-1 = off the input image) and bilinear weights (sum 1 when all
    four are on the image). This is the same 0-based pixel convention that
    ``reproject_interp(order="bilinear")`` uses, so the weights are the ones
    applied to the science image.
    """
    ny, nx = shape_out
    yy, xx = np.mgrid[0:ny, 0:nx]
    with quiet_wcs_logging(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        world = reference_wcs.pixel_to_world_values(xx.ravel().astype(float), yy.ravel().astype(float))
        xi, yi = input_wcs.world_to_pixel_values(*world)
    xi, yi = np.asarray(xi, float), np.asarray(yi, float)
    x0, y0 = np.floor(xi), np.floor(yi)
    fx, fy = xi - x0, yi - y0
    hin, win = shape_in
    idx = np.empty((4, xi.size), np.int64)
    w = np.empty((4, xi.size), float)
    for k, (dx, dy, wk) in enumerate(((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)),
                                      (0, 1, (1 - fx) * fy), (1, 1, fx * fy))):
        cx, cy = x0 + dx, y0 + dy
        ok = np.isfinite(cx) & np.isfinite(cy) & (cx >= 0) & (cy >= 0) & (cx < win) & (cy < hin)
        idx[k] = np.where(ok, np.nan_to_num(cy, nan=0).astype(np.int64) * win
                          + np.nan_to_num(cx, nan=0).astype(np.int64), -1)
        w[k] = np.where(ok & np.isfinite(wk), wk, 0.0)
    return idx, w


@dataclass
class Propagated:
    """What a bilinear resampling does to the uncertainties and quality bits."""

    variance: np.ndarray | None
    """sum_i w_i^2 var_i: the exact per-pixel variance of the resampled value (independent inputs)."""
    bits: np.ndarray | None
    """Bitwise OR of every input pixel that contributed (weight > 0): a bad pixel is never skipped."""
    valid_weight: np.ndarray
    """Fraction of the bilinear weight that fell on valid input pixels (1 = clean, 0 = no data)."""
    noise_correlation: float
    """var(sum over a large area) / sum(per-pixel variance): > 1 because neighbouring output
    pixels share inputs. Multiply VAR by this for aperture sums and PSF fits."""


def propagate_bilinear(input_wcs: WCS, reference_wcs: WCS, shape_out: tuple[int, int], *,
                       valid: np.ndarray, variance: np.ndarray | None = None,
                       bits: np.ndarray | None = None) -> Propagated:
    """Carry variance, quality bits and validity through the same bilinear weights as the image.

    Interpolating a variance map with the image's weights (``sum w v``)
    overstates the per-pixel variance and ignores that resampled pixels are
    correlated; nearest-neighbour bitmasks can miss a bad pixel that did
    contribute. Here variance is ``sum w^2 v``, bits are OR-ed over every
    contributor, and the correlation this introduces is summarised by one
    factor per frame (``noise_correlation``).
    """
    shape_in = valid.shape
    idx, w = bilinear_contributors(input_wcs, reference_wcs, shape_out, shape_in)
    on = idx >= 0
    safe = np.where(on, idx, 0)
    vflat = valid.ravel()[safe] & on
    wsum = w.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        vw = np.where(wsum > 0, (w * vflat).sum(axis=0) / wsum, 0.0)
    contributes = on & (w > 1e-9)

    var_out = None
    corr = 1.0
    if variance is not None:
        v_in = np.where(valid, variance, np.nan).ravel()
        vv = v_in[safe]
        terms = np.where(contributes, w ** 2 * vv, 0.0)
        bad = (contributes & ~np.isfinite(vv)).any(axis=0)
        var_flat = np.where(bad | (wsum <= 0), np.nan, terms.sum(axis=0))
        var_out = var_flat.reshape(shape_out)
        # Large-aperture correlation factor: var(sum_j I'_j) = sum_i c_i^2 var_i with c_i = sum_j w_ji.
        good = np.isfinite(var_flat) & (var_flat > 0)
        if good.any():
            cw = np.bincount(safe[:, good].ravel(), weights=np.where(contributes[:, good], w[:, good], 0).ravel(),
                             minlength=v_in.size)
            vin0 = np.nan_to_num(v_in, nan=0.0)
            num = float((cw ** 2 * vin0).sum())
            den = float(var_flat[good].sum())
            if den > 0 and num > 0:
                corr = num / den
    bits_out = None
    if bits is not None:
        b = np.asarray(bits).astype(np.int64).ravel()[safe]
        bits_out = np.bitwise_or.reduce(np.where(contributes, b, 0), axis=0).reshape(shape_out)
    return Propagated(var_out, bits_out, vw.reshape(shape_out), corr)


def world_offset_arcsec(wcs_a: WCS, xy_a: tuple[float, float], wcs_b: WCS, xy_b: tuple[float, float]) -> float:
    """Angular distance between a pixel position in one frame and one in another."""
    from astropy.coordinates import SkyCoord

    with quiet_wcs_logging(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = wcs_a.pixel_to_world(*xy_a)
        b = wcs_b.pixel_to_world(*xy_b)
    return float(SkyCoord(a).separation(SkyCoord(b)).arcsec)


def centroid(image: np.ndarray, x0: float, y0: float, radius: float = 2.5) -> tuple[float, float] | None:
    """Intensity-weighted centroid in a circular aperture (background ~0 assumed)."""
    yy, xx = np.indices(image.shape)
    r = np.hypot(xx - x0, yy - y0)
    sel = (r <= radius) & np.isfinite(image)
    w = np.where(sel, np.clip(image, 0, None), 0)
    s = w.sum()
    if s <= 0:
        return None
    return float((w * xx).sum() / s), float((w * yy).sum() / s)


def check_alignment(frames: Sequence[np.ndarray], reference_wcs: WCS,
                    star_radec: Sequence[tuple[float, float]], radius_px: float = 2.5) -> list[dict[str, Any]]:
    """Measure where known stationary stars land in each aligned frame.

    Returns per-star, per-frame offsets between the centroid and the star's
    catalogue position: the total in arcsec (``offsets_arcsec``) and the
    components (``offsets_radec_arcsec`` = [dRA*cos(dec), dDec]). Consistent
    offsets across frames indicate an astrometric error in a frame; scatter
    of ~0.1-0.3 px is normal. Summarise with :func:`alignment_summary`.
    """
    out = []
    for ra, dec in star_radec:
        with quiet_wcs_logging():
            x, y = reference_wcs.world_to_pixel_values(ra, dec)
        row = {"ra": ra, "dec": dec, "offsets_arcsec": [], "offsets_radec_arcsec": []}
        for f in frames:
            c = centroid(f, float(x), float(y), radius_px)
            if c is None:
                row["offsets_arcsec"].append(None)
                row["offsets_radec_arcsec"].append(None)
                continue
            with quiet_wcs_logging():
                rc, dc = reference_wcs.pixel_to_world_values(*c)
            dra = ((float(rc) - ra + 180) % 360 - 180) * math.cos(math.radians(dec)) * 3600
            ddec = (float(dc) - dec) * 3600
            row["offsets_arcsec"].append(world_offset_arcsec(reference_wcs, c, reference_wcs, (float(x), float(y))))
            row["offsets_radec_arcsec"].append([dra, ddec])
        out.append(row)
    return out


def alignment_summary(rows: Sequence[dict[str, Any]], max_rms_arcsec: float = 2.0) -> list[dict[str, Any]]:
    """Per-frame astrometric residual statistics from :func:`check_alignment` rows.

    For each frame: number of matched stars, median / RMS / maximum residual
    (arcsec), the mean offset vector (a systematic shift common to all stars)
    and whether the frame passes ``max_rms_arcsec``. A significant mean
    offset means the frame's WCS is shifted; scatter well above the
    centroiding noise points at a rotation/distortion error or bad matches.
    Nothing is corrected automatically: catalogue matches can be wrong.
    """
    n_frames = max((len(r["offsets_arcsec"]) for r in rows), default=0)
    out = []
    for k in range(n_frames):
        vec = np.array([r["offsets_radec_arcsec"][k] for r in rows
                        if k < len(r.get("offsets_radec_arcsec", [])) and r["offsets_radec_arcsec"][k] is not None], float)
        if not len(vec):
            out.append({"frame": k, "n_matched": 0, "passes": False})
            continue
        tot = np.hypot(vec[:, 0], vec[:, 1])
        mean = vec.mean(axis=0)
        sem = vec.std(axis=0, ddof=1) / math.sqrt(len(vec)) if len(vec) > 1 else np.array([np.inf, np.inf])
        rms = float(np.sqrt(np.mean(tot ** 2)))
        out.append({"frame": k, "n_matched": int(len(vec)), "median_arcsec": float(np.median(tot)),
                    "rms_arcsec": rms, "max_arcsec": float(tot.max()),
                    "mean_offset_radec_arcsec": [float(mean[0]), float(mean[1])],
                    "systematic_offset": bool(len(vec) >= 3 and np.any(np.abs(mean) > 3 * sem)),
                    "passes": bool(rms <= max_rms_arcsec)})
    return out
