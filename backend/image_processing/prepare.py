"""Image preparation: invalid pixels, quality masks, background subtraction.

The original science values are never modified in place. Every function
returns new arrays and a list of human-readable processing-history entries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
from astropy.stats import sigma_clipped_stats

from backend.data_acquisition.fits_meta import INFORMATIONAL_FLAGS

# Bits of the per-pixel MASK plane written in processed products.
MASK_NO_COVERAGE = 1   # outside the observation's footprint after reprojection
MASK_FLAGGED = 2       # pixel carries a flag selected by the mask profile
MASK_NONFINITE = 4     # NaN/inf in the input image
MASK_BAD_VARIANCE = 8  # variance missing, non-finite or <= 0

MASK_BITS = {"NO_COVERAGE": MASK_NO_COVERAGE, "FLAGGED": MASK_FLAGGED,
             "NONFINITE": MASK_NONFINITE, "BAD_VARIANCE": MASK_BAD_VARIANCE}

# Which SPHEREx flags make a pixel unusable. Chosen from the flag definitions
# and from how often each flag occurs in real DR1/QR3 cutouts:
#  * minimal  - detector defects / lost data only
#  * standard - + pixels whose value is unreliable (onboard errors,
#               saturation, nonlinearity, persistence, outliers)  [default]
#  * strict   - every non-informational flag (also bloom, halo, ghosts,
#               crosstalk, streaks, transients...)
_MINIMAL = ["NONFUNC", "MISSING_DATA", "HOT", "COLD"]
_STANDARD = _MINIMAL + ["SUR_ERROR", "OVERFLOW", "NONLINEAR", "PERSIST", "OUTLIER"]
MASK_PROFILES: dict[str, list[str] | None] = {
    "none": [],
    "minimal": _MINIMAL,
    "standard": _STANDARD,
    "strict": None,  # all non-informational flags present in the file
}

BACKGROUND_METHODS = ("none", "zodi", "median", "zodi+median", "mesh", "zodi+mesh")
"""``median`` = one sigma-clipped constant (uniform skies, small cutouts); ``mesh`` = a smooth 2-D
background from sigma-clipped medians in boxes (gradients); ``zodi`` = only the pipeline's
zodiacal-light model; ``none`` = keep everything (diffuse-emission studies)."""


@dataclass
class PreparedImage:
    data: np.ndarray
    """Background-subtracted surface brightness; NaN where invalid."""
    variance: np.ndarray | None
    mask: np.ndarray
    """uint8 MASK plane (bits above); 0 = valid."""
    original: np.ndarray
    """Untouched input values (for provenance / 'raw' products)."""
    background_level: float | None = None
    """Residual background removed after the zodi model (MJy/sr; the mesh median for ``mesh``)."""
    zodi_subtracted: np.ndarray | None = None
    """Original minus the ZODI model, unmasked (None without a ZODI model)."""
    background_map: np.ndarray | None = None
    """Residual background that was removed after ZODI (constant or 2-D), MJy/sr."""
    zodi_median: float | None = None
    masked_flags: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)

    @property
    def valid(self) -> np.ndarray:
        return self.mask == 0


def flags_to_mask(flags: np.ndarray | None, definitions: dict[str, dict[str, Any]],
                  profile: str | Iterable[str] = "standard") -> tuple[np.ndarray, list[str]]:
    """Boolean mask of pixels carrying any selected flag; also returns the flag names used."""
    if flags is None:
        return np.zeros((0, 0), bool), []
    if isinstance(profile, str):
        if profile not in MASK_PROFILES:
            raise ValueError(f"Unknown mask profile {profile!r}; use one of {sorted(MASK_PROFILES)}"
                             " or a list of flag names.")
        names = MASK_PROFILES[profile]
        if names is None:
            names = [n for n in definitions if n not in INFORMATIONAL_FLAGS]
    else:
        names = list(profile)
        unknown = [n for n in names if n not in definitions]
        if unknown and definitions:
            raise ValueError(f"Unknown flag names {unknown}; available: {sorted(definitions)}")
    names = [n for n in names if n in definitions]
    bits = 0
    for n in names:
        bits |= int(definitions[n]["value"])
    return (flags & bits) != 0, sorted(names)


def estimate_background(data: np.ndarray, valid: np.ndarray, sigma: float = 3.0) -> float | None:
    """Sigma-clipped median of valid pixels (robust against stars)."""
    vals = data[valid & np.isfinite(data)]
    if vals.size < 5:
        return None
    _, median, _ = sigma_clipped_stats(vals, sigma=sigma, maxiters=10)
    return float(median)


def estimate_background_mesh(data: np.ndarray, valid: np.ndarray, box: int | None = None,
                             sigma: float = 3.0) -> np.ndarray | None:
    """Smooth 2-D background: sigma-clipped medians in boxes, median-filtered and interpolated.

    The box (default: a quarter of the image, at least 8 px = 49") is much
    larger than the PSF, so stars are clipped out, but anything extended on
    that scale (nebulae, large galaxies) is treated as background: use
    ``zodi`` or ``none`` to keep it. Returns None if the image is too small.
    """
    from photutils.background import Background2D, MedianBackground
    from astropy.stats import SigmaClip

    ny, nx = data.shape
    b = box or max(8, min(ny, nx) // 4)
    if min(ny, nx) < 2 * b or (valid & np.isfinite(data)).sum() < 25:
        return None
    try:
        # A 3x3 median filter over the box grid only helps when there are enough boxes.
        fs = (3, 3) if min(ny, nx) // b >= 6 else (1, 1)
        bkg = Background2D(np.where(valid, data, np.nan), (b, b), filter_size=fs,
                           sigma_clip=SigmaClip(sigma=sigma, maxiters=10), bkg_estimator=MedianBackground(),
                           coverage_mask=~valid, fill_value=np.nan, exclude_percentile=50)
    except ValueError:
        return None
    return np.asarray(bkg.background, float)


def prepare_image(
    image: np.ndarray,
    uncertainty: np.ndarray | None = None,
    quality: np.ndarray | None = None,
    *,
    flag_definitions: dict[str, dict[str, Any]] | None = None,
    zodi: np.ndarray | None = None,
    mask_profile: str | Iterable[str] = "standard",
    background: str = "zodi+median",
    uncertainty_is_variance: bool = True,
) -> PreparedImage:
    """Clean one image: invalid pixels, quality mask, background.

    Parameters
    ----------
    image : 2-D array, MJy/sr.
    uncertainty : variance (default) or 1-sigma array (``uncertainty_is_variance=False``).
    quality : SPHEREx FLAGS bitmask.
    flag_definitions : decoded FLAGS header (``fits_meta.decode_flag_definitions``).
    zodi : zodiacal-light model (SPHEREx ``ZODI`` extension).
    mask_profile : ``"none" | "minimal" | "standard" | "strict"`` or a list of flag names.
    background : one of ``BACKGROUND_METHODS``.
        ``zodi`` subtracts the pipeline's zodiacal-light model; ``median``
        subtracts a sigma-clipped median of valid pixels; ``mesh`` a smooth
        2-D background (for gradients). The default does zodi + median: the
        model removes the bulk, the median the small residual (typically
        0.03-0.06 MJy/sr in D6 cutouts), which keeps a time-lapse at a
        steady level. A constant level is appropriate for small cutouts. It
        will remove real signal in fields dominated by extended emission
        (nebulae, galaxies): use ``zodi`` or ``none`` there. The original
        and ZODI-subtracted images are kept either way.
    """
    if background not in BACKGROUND_METHODS:
        raise ValueError(f"background must be one of {BACKGROUND_METHODS}")
    original = np.array(image, dtype=np.float64, copy=True)
    data = original.copy()
    mask = np.zeros(data.shape, np.uint8)
    history: list[str] = []

    nonfinite = ~np.isfinite(data)
    mask[nonfinite] |= MASK_NONFINITE
    if nonfinite.any():
        history.append(f"{int(nonfinite.sum())} non-finite pixels marked invalid")

    variance = None
    if uncertainty is not None:
        u = np.array(uncertainty, dtype=np.float64)
        variance = u if uncertainty_is_variance else u ** 2
        bad_var = ~np.isfinite(variance) | (variance <= 0)
        mask[bad_var & ~nonfinite] |= MASK_BAD_VARIANCE
        variance = np.where(bad_var, np.nan, variance)

    masked_flags: list[str] = []
    if quality is not None and flag_definitions:
        fmask, masked_flags = flags_to_mask(np.asarray(quality, np.int64), flag_definitions, mask_profile)
        mask[fmask] |= MASK_FLAGGED
        label = mask_profile if isinstance(mask_profile, str) else "custom"
        history.append(f"mask profile '{label}' ({', '.join(masked_flags) or 'no flags'}): "
                       f"{int(fmask.sum())} pixels masked")

    zodi_median = None
    zsub = None
    if zodi is not None:
        z = np.asarray(zodi, np.float64)
        zsub = original - z
    if background in ("zodi", "zodi+median", "zodi+mesh"):
        if zodi is None:
            history.append("zodi background requested but no ZODI model available; skipped")
        else:
            z = np.asarray(zodi, np.float64)
            data = data - z
            zodi_median = float(np.nanmedian(z)) if np.isfinite(z).any() else None
            history.append(f"subtracted SPHEREx ZODI model (median {zodi_median:.4f} MJy/sr)")

    level = None
    bmap = None
    if background in ("mesh", "zodi+mesh"):
        bmap = estimate_background_mesh(data, mask == 0)
        if bmap is None:
            history.append("image too small for a 2-D background; used a constant median instead")
            background = "median"
        else:
            data = data - bmap
            level = float(np.nanmedian(bmap))
            history.append(f"subtracted 2-D mesh background (median {level:.4f}, range "
                           f"{np.nanmin(bmap):.4f} to {np.nanmax(bmap):.4f} MJy/sr)")
    if background in ("median", "zodi+median"):
        level = estimate_background(data, mask == 0)
        if level is None:
            history.append("residual background not estimated (too few valid pixels)")
        else:
            data = data - level
            bmap = np.full(data.shape, level)
            history.append(f"subtracted sigma-clipped median background {level:.4f} MJy/sr")

    data = np.where(mask == 0, data, np.nan)
    return PreparedImage(data=data, variance=variance, mask=mask, original=original,
                         background_level=level, zodi_median=zodi_median,
                         zodi_subtracted=zsub, background_map=bmap,
                         masked_flags=masked_flags, history=history)
