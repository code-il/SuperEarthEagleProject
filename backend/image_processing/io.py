"""Reading SPHEREx Level-2 FITS and writing processed products.

SPHEREx L2 files (full images and IRSA cutouts) contain:

=========  =========================================================
PRIMARY    empty data; ``VERSION`` = pipeline version
IMAGE      calibrated surface brightness, MJy/sr (float32); celestial
           WCS with SIP distortion; alternate WCS ``W`` = wavelength
FLAGS      int32 bitmask; bit meanings in ``MSKN/MSKM/MSKD`` cards
VARIANCE   variance of IMAGE, (MJy/sr)^2
ZODI       zodiacal-light model, MJy/sr (already *not* subtracted)
EPSF       table of effective PSFs across the detector
WCS-WAVE   lookup table used by the ``W`` spectral WCS
=========  =========================================================

Nothing is assumed to live in the primary HDU.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

from backend.data_acquisition.fits_meta import decode_flag_definitions, quiet_wcs_logging


@dataclass
class SpherexImage:
    """One SPHEREx exposure (or cutout) loaded into memory, in native pixels."""

    image: np.ndarray
    """Surface brightness (MJy/sr), float64; NaN where the pipeline gave no value."""
    variance: np.ndarray | None
    flags: np.ndarray | None
    """int64 bitmask, same shape as ``image``."""
    zodi: np.ndarray | None
    wcs: WCS
    """Celestial WCS of ``image`` (TAN + SIP)."""
    header: fits.Header
    flag_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    wavelength: np.ndarray | None = None
    """Central wavelength (um) of every pixel, from the spectral WCS."""
    bandwidth: np.ndarray | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.image.shape  # type: ignore[return-value]


def _celestial_wcs(header: fits.Header) -> WCS:
    with quiet_wcs_logging(), warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        return WCS(header)


def _wavelength_maps(hdul: fits.HDUList, header: fits.Header, shape: tuple[int, int]):
    if "WCS-WAVE" not in [h.name for h in hdul]:
        return None, None
    try:
        with quiet_wcs_logging(), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ww = WCS(header, fobj=hdul, key="W")
            yy, xx = np.indices(shape)
            wl, bw = ww.pixel_to_world_values(xx.astype(float), yy.astype(float))
        return np.asarray(wl, dtype=float), np.asarray(bw, dtype=float)
    except Exception:
        return None, None


def extract_science_image(hdul: fits.HDUList) -> SpherexImage:
    """Pull science, uncertainty, quality, background, WCS and wavelength out of an open HDUList."""
    names = [h.name for h in hdul]
    if "IMAGE" not in names:
        raise ValueError(f"Not a SPHEREx L2 image: extensions are {names}")
    hdr = hdul["IMAGE"].header.copy()
    image = np.array(hdul["IMAGE"].data, dtype=np.float64)
    variance = np.array(hdul["VARIANCE"].data, dtype=np.float64) if "VARIANCE" in names else None
    flags = np.array(hdul["FLAGS"].data, dtype=np.int64) if "FLAGS" in names else None
    zodi = np.array(hdul["ZODI"].data, dtype=np.float64) if "ZODI" in names else None
    defs = decode_flag_definitions(hdul["FLAGS"].header) if "FLAGS" in names else {}
    wl, bw = _wavelength_maps(hdul, hdr, image.shape)
    wl_info: dict[str, Any] = {"wavelength_source": "WCS-WAVE (approximate lookup)" if wl is not None else None}
    from backend.data_acquisition.calibration import science_wavelength_maps
    sci = science_wavelength_maps(hdr, image.shape, hdr.get("DETECTOR"), hdul[0].header.get("VERSION"), wl)
    if sci is not None:
        wl, bw, wl_info = sci
    meta = {
        "obs_id": hdr.get("OBSID"),
        "detector": hdr.get("DETECTOR"),
        "date_obs": hdr.get("DATE-OBS"),
        "mjd_obs": hdr.get("MJD-OBS"),
        "mjd_avg": hdr.get("MJD-AVG"),
        "exposure_time_s": hdr.get("XPOSURE"),
        "bunit": hdr.get("BUNIT"),
        "psf_fwhm_arcsec": hdr.get("PSF_FWHM"),
        "l1_dqa": hdr.get("L1DQAFLG"),
        "l2_dqa": hdr.get("L2DQAFLG"),
        "pipeline_version": hdul[0].header.get("VERSION"),
        "psf_header_fix_needed": psf_header_fix_needed(hdul[0].header.get("VERSION")),
        **wl_info,
    }
    return SpherexImage(image=image, variance=variance, flags=flags, zodi=zodi,
                        wcs=_celestial_wcs(hdr), header=hdr, flag_definitions=defs,
                        wavelength=wl, bandwidth=bw, meta=meta)


def psf_header_fix_needed(version: str | None) -> bool:
    """True if this image's EPSF header has the plane-order bug (IRSA notice, 2026-03-31).

    Quick Release images from pipeline 6.4-6.5.5 list the PSF cube planes in
    the wrong order (correct plane = y * n_cols + x + 1). Fixed files carry
    "+psffix1" in VERSION; 6.5.6 and later are unaffected. The engine only
    uses the scalar PSF_FWHM, not the EPSF planes, but anything that reads
    the EPSF extension must re-index it when this is True.
    """
    if not version:
        return False
    v = str(version)
    if "psffix" in v:
        return False
    try:
        parts = tuple(int(x) for x in v.split("+")[0].split(".")[:3])
    except ValueError:
        return False
    return (6, 4, 0) <= parts + (0,) * (3 - len(parts)) <= (6, 5, 5)


def load_spherex_fits(path: str | Path) -> SpherexImage:
    """Load a SPHEREx L2 FITS (full image or cutout) from disk."""
    with fits.open(path, memmap=False) as hdul:
        img = extract_science_image(hdul)
    img.meta["source_path"] = str(path)
    return img


def wcs_to_dict(wcs: WCS) -> dict[str, Any]:
    """JSON-friendly celestial WCS (FITS keywords) for the viewer."""
    hdr = wcs.to_header(relax=True)
    out: dict[str, Any] = {}
    for k in hdr:
        v = hdr[k]
        if isinstance(v, (np.floating, np.integer)):
            v = v.item()
        out[k] = v
    return out


def flag_definition_cards(definitions: dict[str, dict[str, Any]]) -> list[tuple[str, Any, str]]:
    """Re-create the ``MSKN/MSKM/MSKD`` cards so processed FLAGS planes stay self-describing."""
    cards = []
    for i, (name, d) in enumerate(sorted(definitions.items(), key=lambda x: x[1]["value"])):
        cards += [(f"MSKN{i:04d}", name, "flag name"),
                  (f"MSKM{i:04d}", int(d["value"]), "flag bit value"),
                  (f"MSKD{i:04d}", str(d.get("description", ""))[:56], "")]
    return cards
