"""SPHEREx calibration products: per-pixel wavelength (CWAVE/CBAND) and pixel solid angle.

The ``WCS-WAVE`` table inside every spectral image is a compact
approximation "intended for visualization"; IRSA recommends the CWAVE and
CBAND layers of the per-detector *Spectral WCS* calibration product for
science (SPHEREx Explanatory Supplement SSDC-DP-017, sec. 4.3.1). Those
files are 2040x2040 maps in the L2 image pixel frame (``DETCOORD='sky'``),
one per detector and calibration version, served from

    https://irsa.ipac.caltech.edu/ibe/data/spherex/<release>/spectral_wcs/<token>/<det>/
        spectral_wcs_D<det>_spx_<token>.fits

The calibration version must match the one the image was made with: the
QR2-era file (SpecCal 2025-09-01) and the R7 file used by DR1/QR3 (SpecCal
2026-06-04) differ by a constant ~+0.0065 um at 4-5 um. We choose the file
by the image's pipeline ``VERSION`` and then *verify* the choice: the
image's own WCS-WAVE values must agree with CWAVE to within 3x the
lookup error quoted in the calibration header (WLERRP99). If they do not,
the calibration is not the image's and the WCS-WAVE values are kept, with
a note, rather than silently applying a mismatched calibration.

Cutouts carry an alternate WCS ``A`` ("0-based active pixel coordinates")
that maps each cutout pixel to its detector pixel.
"""
from __future__ import annotations

import logging
import threading
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from .cache import _replace_with_retry
from .config import get_settings
from .fits_meta import quiet_wcs_logging
from .http import get_session, request_with_retries

log = logging.getLogger(__name__)

IBE_ROOT = "https://irsa.ipac.caltech.edu/ibe/data/spherex"
DETECTOR_SIZE = 2040


@dataclass(frozen=True)
class CalibrationSet:
    min_pipeline_major: int
    release_dir: str
    spectral_wcs_token: str
    sapm_token: str
    description: str


# Newest first. Verified against DR1/QR3 images (pipeline 7.0.5): CWAVE - WCS-WAVE
# median 0.0003 um (lookup error p99 0.0013 um); with the v4 file the offset is 0.0067 um.
CALIBRATION_SETS: tuple[CalibrationSet, ...] = (
    CalibrationSet(7, "dr1", "cal-swcs-v5-2026-191", "cal-sapm-v3-2026-191",
                   "R7 calibration used by DR1 and QR3 (SpecCal 2026-06-04)"),
    CalibrationSet(0, "qr2", "cal-wcs-v4-2025-254", "cal-sapm-v2-2025-164",
                   "QR1/QR2-era calibration (SpecCal 2025-09-01)"),
)


def calibration_for_version(pipeline_version: str | None) -> CalibrationSet:
    try:
        major = int(str(pipeline_version).split(".")[0])
    except (TypeError, ValueError):
        major = 99
    return next(c for c in CALIBRATION_SETS if major >= c.min_pipeline_major)


def _cal_dir() -> Path:
    return get_settings().cache_dir / "calibration"


_lock = threading.Lock()


def _fetch(url: str, path: Path) -> Path:
    with _lock:
        if path.exists() and path.stat().st_size > 0:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        resp = request_with_retries(get_session(), "GET", url, timeout=(10, 300), retries=2, backoff=2.0)
        tmp.write_bytes(resp.content)
        with fits.open(tmp) as h:  # validate before moving into place
            h.verify("silentfix")
        _replace_with_retry(tmp, path)
        return path


def spectral_wcs_path(detector: int, cal: CalibrationSet) -> Path:
    name = f"spectral_wcs_D{detector}_spx_{cal.spectral_wcs_token}.fits"
    path = _cal_dir() / cal.release_dir / name
    url = f"{IBE_ROOT}/{cal.release_dir}/spectral_wcs/{cal.spectral_wcs_token}/{detector}/{name}"
    return _fetch(url, path)


def sapm_path(detector: int, cal: CalibrationSet) -> Path:
    name = f"solid_angle_pixel_map_D{detector}_spx_{cal.sapm_token}.fits"
    path = _cal_dir() / cal.release_dir / name
    url = f"{IBE_ROOT}/{cal.release_dir}/solid_angle_pixel_map/{cal.sapm_token}/{detector}/{name}"
    return _fetch(url, path)


@lru_cache(maxsize=12)
def _load_spectral_wcs(path: str) -> tuple[np.ndarray, np.ndarray, float]:
    with fits.open(path, memmap=False) as h:
        return (np.array(h["CWAVE"].data, dtype=np.float32), np.array(h["CBAND"].data, dtype=np.float32),
                float(h[0].header.get("WLERRP99", 0.0013)))


def detector_pixels(header: fits.Header, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray] | None:
    """Detector (x, y) indices of every cutout pixel, from the cutout's ``A`` WCS."""
    if "CRPIX1A" not in header:
        return None
    with quiet_wcs_logging(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wa = WCS(header, key="A")
        yy, xx = np.indices(shape)
        dx, dy = wa.pixel_to_world_values(xx.astype(float), yy.astype(float))
    return np.rint(dx).astype(int), np.rint(dy).astype(int)


def science_wavelength_maps(header: fits.Header, shape: tuple[int, int], detector: int | None,
                            pipeline_version: str | None, lookup_wavelength: np.ndarray | None = None
                            ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]] | None:
    """Per-pixel central wavelength and bandwidth (um) from CWAVE/CBAND, verified.

    Returns ``(wavelength, bandwidth, info)`` or None when the calibration
    cannot be used (no detector pixel mapping, download failure, or the
    calibration disagrees with the image's own lookup table).
    """
    if detector is None or not get_settings().use_spectral_wcs_calibration:
        return None
    pix = detector_pixels(header, shape)
    if pix is None:
        return None
    cal = calibration_for_version(pipeline_version)
    try:
        cw, cb, err99 = _load_spectral_wcs(str(spectral_wcs_path(int(detector), cal)))
    except Exception as exc:
        log.warning("spectral WCS calibration unavailable (%s); using WCS-WAVE", exc)
        return None
    ix, iy = pix
    ok = (ix >= 0) & (ix < DETECTOR_SIZE) & (iy >= 0) & (iy < DETECTOR_SIZE)
    wl = np.full(shape, np.nan)
    bw = np.full(shape, np.nan)
    wl[ok] = cw[iy[ok], ix[ok]]
    bw[ok] = cb[iy[ok], ix[ok]]
    info = {"wavelength_source": "CWAVE/CBAND", "calibration": cal.spectral_wcs_token,
            "calibration_release_dir": cal.release_dir}
    if lookup_wavelength is not None:
        d = np.abs(wl - lookup_wavelength)[ok & np.isfinite(lookup_wavelength)]
        if d.size:
            med = float(np.median(d))
            info["median_abs_diff_vs_wcs_wave_um"] = round(med, 5)
            if med > 3 * err99:
                log.warning("CWAVE (%s) disagrees with WCS-WAVE by %.4f um: calibration mismatch, keeping WCS-WAVE",
                            cal.spectral_wcs_token, med)
                return None
    if not ok.all():
        # pixels outside the detector: fall back to the lookup table there
        if lookup_wavelength is not None:
            wl[~ok] = lookup_wavelength[~ok]
        info["pixels_off_detector"] = int((~ok).sum())
    return wl, bw, info
