"""Metadata extraction from SPHEREx Level-2 FITS files (full images or cutouts).

Extensions (verified against real DR1 cutouts): PRIMARY (empty), IMAGE
(MJy/sr), FLAGS (int32 bitmask), VARIANCE, ZODI, EPSF (PSF table),
WCS-WAVE (lookup table for the spectral WCS, key ``W``).

The FLAGS header documents its own bits (``MSKNnnnn`` name, ``MSKMnnnn``
value, ``MSKDnnnn`` description), so flags are decoded from each file rather
than from a hard-coded table -- this keeps working if a future release adds
or renumbers bits.
"""
from __future__ import annotations

import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

# Bits that describe the pixel rather than a problem with it.
INFORMATIONAL_FLAGS = {"FULLSAMPLE", "SOURCE"}

_HEADER_KEYS = {
    "DATE-OBS": "date_obs", "DATE-END": "date_end", "MJD-OBS": "mjd_obs", "MJD-AVG": "mjd_avg",
    "MJD-END": "mjd_end", "XPOSURE": "exposure_time_s", "TELAPSE": "elapsed_time_s",
    "DETECTOR": "detector", "OBSID": "obs_id", "EXPIDN": "exposure_id", "BUNIT": "bunit",
    "L1DQAFLG": "l1_dqa", "L2DQAFLG": "l2_dqa", "PSF_FWHM": "psf_fwhm", "SPS_TARGET": "survey_field",
    "SPS_FLAGS": "survey_flags", "DETCOORD": "detector_coords", "NON_SURVEY": "non_survey",
    "ASTRONETSOLVED": "astrometry_solved", "MATCH_NSTARS": "astrometry_n_stars",
}


@contextmanager
def quiet_wcs_logging():
    """Silence astropy's per-file SIP INFO message.

    SPHEREx headers carry SIP distortion terms without the ``-SIP`` CTYPE
    suffix. astropy applies them anyway (which is correct here: verified
    against stellar positions) but logs a long message for every file.
    """
    from astropy import log as astropy_log

    prev = astropy_log.level
    astropy_log.setLevel("WARNING")
    try:
        yield
    finally:
        astropy_log.setLevel(prev)


def decode_flag_definitions(header: fits.Header) -> dict[str, dict[str, Any]]:
    """``{name: {"bit": int, "value": int, "description": str}}`` from a FLAGS header."""
    defs: dict[str, dict[str, Any]] = {}
    for key in header:
        if key.startswith("MSKN"):
            idx = key[4:]
            name = str(header[key]).strip()
            value = header.get(f"MSKM{idx}")
            if value is None:
                continue
            value = int(value)
            defs[name] = {
                "bit": int(np.log2(value)) if value > 0 else None,
                "value": value,
                "description": str(header.get(f"MSKD{idx}", "")).strip(),
            }
    return defs


def decode_flag_value(value: int, definitions: dict[str, dict[str, Any]]) -> list[str]:
    value = int(value)
    return sorted(name for name, d in definitions.items() if value & d["value"])


def _jsonable(v: Any) -> Any:
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    return v


def extract_fits_metadata(source: str | Path | fits.HDUList, ra: float | None = None,
                          dec: float | None = None) -> dict[str, Any]:
    """Read observation, quality and wavelength metadata from a SPHEREx L2 FITS.

    If ``ra``/``dec`` are given, flags and wavelength are evaluated at that
    sky position (3x3 pixel box for flags); otherwise at the image centre.
    """
    close = False
    if not isinstance(source, fits.HDUList):
        hdul = fits.open(source, memmap=False)
        close = True
    else:
        hdul = source
    try:
        return _extract(hdul, ra, dec)
    finally:
        if close:
            hdul.close()


def _extract(hdul: fits.HDUList, ra: float | None, dec: float | None) -> dict[str, Any]:
    names = [h.name for h in hdul]
    if "IMAGE" not in names:
        raise ValueError(f"Not a SPHEREx L2 image: extensions are {names}")
    im = hdul["IMAGE"]
    hdr = im.header
    out: dict[str, Any] = {"extensions": names}
    out["pipeline_version"] = hdul[0].header.get("VERSION")
    for k, dest in _HEADER_KEYS.items():
        out[dest] = _jsonable(hdr.get(k)) if k in hdr else None
    out["shape"] = list(im.data.shape) if im.data is not None else None
    out["l2_counts"] = {k[3:].strip().lower(): _jsonable(hdr[k]) for k in hdr if k.startswith("L2 N_")}

    with quiet_wcs_logging(), warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        warnings.simplefilter("ignore", UserWarning)
        wcs = WCS(hdr)
        ny, nx = im.data.shape
        if ra is not None and dec is not None:
            px, py = wcs.world_to_pixel_values(ra, dec)
            px, py = float(px), float(py)
        else:
            px, py = (nx - 1) / 2.0, (ny - 1) / 2.0
        c = wcs.pixel_to_world_values((nx - 1) / 2.0, (ny - 1) / 2.0)
        out["center_ra"], out["center_dec"] = float(c[0]), float(c[1])
        try:
            scale = np.sqrt(np.abs(np.linalg.det(wcs.pixel_scale_matrix))) * 3600.0
            out["pixel_scale_arcsec"] = float(scale)
        except Exception:
            out["pixel_scale_arcsec"] = None
        out["target_pixel"] = [px, py]
        inside = -0.5 <= px < nx - 0.5 and -0.5 <= py < ny - 0.5
        out["target_in_image"] = bool(inside)

        # Spectral WCS: wavelength and bandwidth at the target pixel
        out["wavelength_um"] = out["bandwidth_um"] = None
        if "WCS-WAVE" in names and inside:
            try:
                wwcs = WCS(hdr, fobj=hdul, key="W")
                wl, bw = wwcs.pixel_to_world_values(px, py)
                out["wavelength_um"] = float(wl)
                out["bandwidth_um"] = float(bw)
            except Exception as exc:  # keep going; wavelength is optional
                out["wavelength_error"] = str(exc)

    # Quality flags
    flags: dict[str, Any] = {"available": "FLAGS" in names}
    if "FLAGS" in names and hdul["FLAGS"].data is not None:
        defs = decode_flag_definitions(hdul["FLAGS"].header)
        fdata = np.asarray(hdul["FLAGS"].data).astype(np.int64)
        flags["definitions"] = defs
        frac = {}
        for name, d in defs.items():
            n = int(np.count_nonzero(fdata & d["value"]))
            if n:
                frac[name] = round(n / fdata.size, 5)
        flags["fraction_by_flag"] = frac
        problem_mask = sum(d["value"] for n, d in defs.items() if n not in INFORMATIONAL_FLAGS)
        flags["fraction_with_problem_flags"] = round(
            float(np.count_nonzero(fdata & problem_mask)) / fdata.size, 5)
        if out["target_in_image"]:
            ix, iy = int(round(out["target_pixel"][0])), int(round(out["target_pixel"][1]))
            box = fdata[max(iy - 1, 0): iy + 2, max(ix - 1, 0): ix + 2]
            combined = int(np.bitwise_or.reduce(box.ravel())) if box.size else 0
            centre_val = int(fdata[iy, ix])
            flags["target_pixel_value"] = centre_val
            flags["target_pixel_flags"] = decode_flag_value(centre_val, defs)
            flags["target_box_flags"] = decode_flag_value(combined, defs)
        else:
            flags["target_pixel_flags"] = flags["target_box_flags"] = []
    out["flags"] = flags
    # Problem flags at/around the target: the record's ``quality_flags``
    out["quality_flags"] = sorted(set(flags.get("target_box_flags", [])) - INFORMATIONAL_FLAGS)
    if out.get("l2_dqa") not in (None, "Pass"):
        out["quality_flags"].append(f"L2DQA:{out['l2_dqa']}")
    if out.get("l1_dqa") not in (None, "Pass"):
        out["quality_flags"].append(f"L1DQA:{out['l1_dqa']}")
    return out


def is_valid_spherex_fits(path: str | Path) -> bool:
    """Cheap integrity check used before trusting a cached file."""
    try:
        with fits.open(path, memmap=False) as hdul:
            return "IMAGE" in [h.name for h in hdul] and hdul["IMAGE"].data is not None
    except Exception:
        return False
