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

    Returns per-star, per-frame offsets (arcsec) between the centroid and the
    star's catalogue position. Consistent offsets across frames indicate an
    astrometric error in a frame; scatter of ~0.1-0.3 px is normal.
    """
    out = []
    for ra, dec in star_radec:
        with quiet_wcs_logging():
            x, y = reference_wcs.world_to_pixel_values(ra, dec)
        row = {"ra": ra, "dec": dec, "offsets_arcsec": []}
        for f in frames:
            c = centroid(f, float(x), float(y), radius_px)
            row["offsets_arcsec"].append(
                None if c is None else world_offset_arcsec(reference_wcs, c, reference_wcs, (float(x), float(y))))
        out.append(row)
    return out
