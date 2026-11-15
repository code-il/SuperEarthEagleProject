"""End-to-end processing of SPHEREx observations onto a common grid.

Flow for one observation::

    cutout FITS --load--> SpherexImage --prepare--> PreparedImage
               --reproject--> ProcessedObservation --save--> FITS + PNG

``process_series`` does this for every observation of a position (fetching
cutouts through the data acquisition module), then ``make_stacks`` and
``compare`` build co-adds, difference and ratio images on the same grid.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from astropy.io import fits
from astropy.stats import mad_std
from astropy.time import Time
from astropy.wcs import WCS, FITSFixedWarning

from backend.data_acquisition import (ObservationRecord, download_cutouts, get_settings,
                                      search_observations)
from backend.data_acquisition.cache import stable_hash
from backend.data_acquisition.fits_meta import quiet_wcs_logging

from .align import (NATIVE_PIXEL_SCALE_ARCSEC, cutout_size_for_grid, make_reference_wcs,
                    propagate_bilinear, reproject_bitmask, reproject_observation)
from .channels import channel_for, channel_width_um
from .combine import (STACK_METHODS, ComparisonResult, divide_images, psf_match,
                      stack_observations, subtract_images)
from .display import compute_limits, generate_display_image, make_animation, save_png, upscale
from .io import SpherexImage, flag_definition_cards, load_spherex_fits, wcs_to_dict
from .prepare import MASK_BITS, MASK_FLAGGED, MASK_NO_COVERAGE, MASK_NONFINITE, prepare_image

log = logging.getLogger(__name__)

PROCESSING_VERSION = "1.3"
# 1.2: per-pixel wavelengths from CWAVE/CBAND calibration
# 1.3: variance resampled with squared weights, flags OR-ed over contributors, ZSUB/WGT planes,
#      per-channel display limits, wavelength-split stacks
MASK_INTERP_EDGE = 16  # valid input, but interpolation touched an invalid neighbour
ALL_MASK_BITS = {**MASK_BITS, "INTERP_EDGE": MASK_INTERP_EDGE}


def processed_root() -> Path:
    return get_settings().cache_dir / "processed"


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------
@dataclass
class ProcessedObservation:
    """One observation resampled onto the common grid."""

    observation_id: str
    observation_time: str | None
    mjd: float | None
    detector: int | None
    wavelength: float | None
    """Wavelength (um) at the grid centre in this exposure."""
    bandwidth: float | None
    data: np.ndarray
    """Background-subtracted surface brightness (MJy/sr); NaN where mask != 0."""
    variance: np.ndarray | None
    mask: np.ndarray
    flags: np.ndarray
    raw: np.ndarray
    """Reprojected original values: no background subtraction, no masking."""
    wavelength_map: np.ndarray | None
    wcs: WCS
    zodi_subtracted: np.ndarray | None = None
    """Reprojected original minus the ZODI model (no further background, no masking)."""
    interp_weight: np.ndarray | None = None
    """Fraction of each pixel's interpolation weight that came from valid input pixels."""
    noise_correlation: float | None = None
    """var(sum over a large area) / sum(VAR): multiply VAR by this for aperture sums / PSF fits."""
    psf_fwhm: float | None = None
    background_level: float | None = None
    zodi_median: float | None = None
    quality_flags: list[str] = field(default_factory=list)
    masked_flags: list[str] = field(default_factory=list)
    flag_definitions: dict[str, Any] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    source_path: str | None = None
    source_sha256: str | None = None
    data_release: str | None = None
    processing: dict[str, Any] = field(default_factory=dict)
    fits_path: str | None = None
    png_path: str | None = None

    @property
    def valid(self) -> np.ndarray:
        return self.mask == 0

    @property
    def valid_fraction(self) -> float:
        return float(self.valid.mean())

    @property
    def channel(self):
        return channel_for(self.detector, self.wavelength)


def _quiet_wcs(header) -> WCS:
    with quiet_wcs_logging(), warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        return WCS(header)


def _pixel_scale_arcsec(w: WCS) -> float:
    return float(np.sqrt(abs(np.linalg.det(w.pixel_scale_matrix))) * 3600)


# --------------------------------------------------------------------------
# One observation
# --------------------------------------------------------------------------
def process_observation(
    source: str | Path | SpherexImage,
    reference_wcs: WCS,
    shape: tuple[int, int],
    *,
    observation_id: str | None = None,
    observation_time: str | None = None,
    data_release: str | None = None,
    mask_profile: str | Iterable[str] = "standard",
    background: str = "zodi+median",
    method: str = "interp",
    source_sha256: str | None = None,
) -> ProcessedObservation:
    """Prepare one SPHEREx image and resample every plane onto ``reference_wcs``."""
    img = source if isinstance(source, SpherexImage) else load_spherex_fits(source)
    prep = prepare_image(img.image, img.variance, img.flags, flag_definitions=img.flag_definitions,
                         zodi=img.zodi, mask_profile=mask_profile, background=background)
    src = img.meta.get('source_path')
    history = [f"source: {Path(src).name if src else 'in-memory image'}"] + prep.history

    sci, fp = reproject_observation(prep.data, img.wcs, reference_wcs, shape, method)
    raw, _ = reproject_observation(prep.original, img.wcs, reference_wcs, shape, method)
    zsub = None
    if prep.zodi_subtracted is not None:
        zsub, _ = reproject_observation(prep.zodi_subtracted, img.wcs, reference_wcs, shape, method)
    var = weight = corr = None
    if method == "interp":
        # Same bilinear weights as the science image: variance sum(w^2 var), bits OR-ed over all
        # contributing input pixels, and the valid fraction of the weight.
        pr = propagate_bilinear(img.wcs, reference_wcs, shape, valid=prep.valid, variance=prep.variance,
                                bits=prep.mask.astype(np.int64))
        var, weight, corr = pr.variance, pr.valid_weight, pr.noise_correlation
        src_mask = pr.bits.astype(np.uint8)
        flags = (propagate_bilinear(img.wcs, reference_wcs, shape, valid=prep.valid, bits=img.flags).bits
                 if img.flags is not None else np.zeros(shape, np.int64))
        history.append(f"variance resampled with squared bilinear weights; quality bits OR-ed over the 4 "
                       f"contributing pixels; noise correlation factor {corr:.2f} (multiply VAR by it for "
                       "sums over areas >> 1 px)")
    else:
        if prep.variance is not None:
            v_in = np.where(prep.valid, prep.variance, np.nan)
            var, _ = reproject_observation(v_in, img.wcs, reference_wcs, shape, method)
            history.append(f"variance resampled with the '{method}' method (approximate for this method)")
        flags = (reproject_bitmask(img.flags, img.wcs, reference_wcs, shape)
                 if img.flags is not None else np.zeros(shape, np.int64))
        src_mask = reproject_bitmask(prep.mask.astype(np.int64), img.wcs, reference_wcs, shape).astype(np.uint8)
    wl_map = None
    if img.wavelength is not None:
        wl_map, _ = reproject_observation(img.wavelength, img.wcs, reference_wcs, shape, "interp")

    mask = src_mask.copy()
    no_cov = ~(fp > 0)
    mask[no_cov] = MASK_NO_COVERAGE
    edge = (mask == 0) & ~np.isfinite(sci)
    mask[edge] |= MASK_INTERP_EDGE
    if var is not None:
        edge_v = (mask == 0) & ~(np.isfinite(var) & (var > 0))
        mask[edge_v] |= MASK_INTERP_EDGE
    sci = np.where(mask == 0, sci, np.nan)
    if var is not None:
        var = np.where(mask == 0, var, np.nan)
    history.append(f"reprojected ({method}) onto {shape[1]}x{shape[0]} grid at "
                   f"{_pixel_scale_arcsec(reference_wcs):.2f}\"/px; "
                   f"{int(no_cov.sum())} px outside footprint, {int(edge.sum())} px at interpolation edges")

    # wavelength and quality at the grid centre (= target)
    cy, cx = (shape[0] - 1) // 2, (shape[1] - 1) // 2
    wl = float(wl_map[cy, cx]) if wl_map is not None and np.isfinite(wl_map[cy, cx]) else None
    bw = None
    if img.bandwidth is not None:
        with quiet_wcs_logging():
            x, y = img.wcs.world_to_pixel_values(*reference_wcs.wcs.crval)
        ix, iy = int(round(float(x))), int(round(float(y)))
        if 0 <= iy < img.shape[0] and 0 <= ix < img.shape[1]:
            bw = float(img.bandwidth[iy, ix])
    from backend.data_acquisition.fits_meta import INFORMATIONAL_FLAGS, decode_flag_value

    box = flags[max(cy - 1, 0):cy + 2, max(cx - 1, 0):cx + 2]
    qf = sorted(set(decode_flag_value(int(np.bitwise_or.reduce(box.ravel())), img.flag_definitions))
                - INFORMATIONAL_FLAGS) if box.size else []
    for k in ("l1_dqa", "l2_dqa"):
        if img.meta.get(k) not in (None, "Pass"):
            qf.append(f"{k.upper()}:{img.meta[k]}")

    det = img.meta.get("detector")
    obs_id = observation_id or (f"{img.meta.get('obs_id')}_D{det}" if img.meta.get("obs_id") else "unknown")
    t = observation_time or (img.meta.get("date_obs") + "Z" if img.meta.get("date_obs") else None)
    mjd = img.meta.get("mjd_obs")
    return ProcessedObservation(
        observation_id=obs_id, observation_time=t, mjd=float(mjd) if mjd is not None else None,
        detector=int(det) if det is not None else None, wavelength=wl, bandwidth=bw,
        data=sci, variance=var, mask=mask, flags=flags, raw=raw, wavelength_map=wl_map,
        wcs=reference_wcs, zodi_subtracted=zsub, interp_weight=weight, noise_correlation=corr,
        psf_fwhm=img.meta.get("psf_fwhm_arcsec"),
        background_level=prep.background_level, zodi_median=prep.zodi_median,
        quality_flags=qf, masked_flags=prep.masked_flags, flag_definitions=img.flag_definitions,
        history=history, source_path=img.meta.get("source_path"), source_sha256=source_sha256,
        data_release=data_release,
        processing={"mask_profile": mask_profile if isinstance(mask_profile, str) else list(mask_profile),
                    "background": background, "reproject_method": method,
                    "pixel_scale_arcsec": _pixel_scale_arcsec(reference_wcs),
                    "version": PROCESSING_VERSION,
                    "wavelength_source": img.meta.get("wavelength_source"),
                    "wavelength_calibration": img.meta.get("calibration"),
                    "pipeline_version": img.meta.get("pipeline_version"),
                    "psf_header_fix_needed": bool(img.meta.get("psf_header_fix_needed"))},
    )


# --------------------------------------------------------------------------
# FITS I/O for processed products
# --------------------------------------------------------------------------
def _writeto(hdul: fits.HDUList, path: Path) -> None:
    """Write FITS, letting astropy shorten over-long header comments silently."""
    from astropy.io.fits.verify import VerifyWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", VerifyWarning)
        hdul.writeto(path, overwrite=True)


def _image_hdu(data, name: str, wcs_header: fits.Header, bunit: str | None = None, dtype=None) -> fits.ImageHDU:
    arr = np.asarray(data if dtype is None else np.asarray(data).astype(dtype))
    hdu = fits.ImageHDU(arr, header=wcs_header.copy(), name=name)
    if bunit:
        hdu.header["BUNIT"] = bunit
    return hdu


def save_processed_observation(image: ProcessedObservation, metadata: dict[str, Any] | None,
                               output_path: str | Path) -> Path:
    """Write a processed observation as a multi-extension FITS.

    Extensions: SCI (MJy/sr, background-subtracted, NaN = invalid), VAR
    (per-pixel variance; NOISECOR card for area sums), MASK (uint8; bits in
    MASKB* cards), FLAGS (original SPHEREx bits, OR of the contributing
    input pixels; definitions in MSKN/MSKM/MSKD cards), RAW (reprojected
    calibrated values, untouched), ZSUB (RAW minus the ZODI model), WGT
    (valid fraction of the interpolation weight) and WAVE (um per pixel). Every extension
    carries the common grid's celestial WCS. The primary header holds
    provenance and the processing history.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w = image.wcs.to_header()
    ph = fits.Header()
    ph["OBS_ID"] = (image.observation_id, "observation ID (<obs_id>_D<detector>)")
    ph["DETECTOR"] = (image.detector, "SPHEREx detector array 1-6")
    ph["DATE-OBS"] = ((image.observation_time or "").rstrip("Z"), "exposure start (UTC)")
    if image.mjd is not None:
        ph["MJD-OBS"] = (image.mjd, "exposure start (MJD, UTC)")
    if image.wavelength is not None:
        ph["WAVELEN"] = (round(image.wavelength, 5), "[um] wavelength at grid centre")
    if image.bandwidth is not None:
        ph["BANDWID"] = (round(image.bandwidth, 5), "[um] bandwidth at grid centre")
    ch = image.channel
    if ch is not None:
        ph["CHANNEL"] = (ch.index, "nominal SPHEREx channel 0-101 (see channels.py)")
    if image.psf_fwhm is not None:
        ph["PSF_FWHM"] = (image.psf_fwhm, "[arcsec] PSF FWHM from source header")
    if image.processing.get("wavelength_source"):
        ph["WAVESRC"] = (str(image.processing["wavelength_source"])[:40], "per-pixel wavelength source")
    if image.processing.get("wavelength_calibration"):
        ph["WAVECAL"] = (str(image.processing["wavelength_calibration"]), "spectral WCS calibration file token")
    if image.processing.get("pipeline_version"):
        ph["PIPEVER"] = (str(image.processing["pipeline_version"]), "SPHEREx L2 pipeline VERSION of source")
    ph["PSFFIX"] = (bool(image.processing.get("psf_header_fix_needed")), "EPSF plane order needs IRSA fix")
    if image.data_release:
        ph["RELEASE"] = image.data_release
    ph["BUNIT"] = "MJy / sr"
    p = image.processing
    ph["MASKPROF"] = (str(p.get("mask_profile")), "quality-flag mask profile")
    ph["BKGMETH"] = (p.get("background"), "background method")
    if image.background_level is not None:
        ph["BKGLEVEL"] = (image.background_level, "[MJy/sr] residual median background removed")
    if image.zodi_median is not None:
        ph["ZODIMED"] = (image.zodi_median, "[MJy/sr] median of subtracted ZODI model")
    ph["REPROJ"] = (p.get("reproject_method"), "reprojection method")
    if image.noise_correlation is not None:
        ph["NOISECOR"] = (round(float(image.noise_correlation), 4), "VAR x this = variance of large-area sums")
    ph["PIXSCALE"] = (round(p.get("pixel_scale_arcsec", 0), 4), "[arcsec/px] output grid")
    ph["PROCVER"] = (PROCESSING_VERSION, "SPHEREx Explorer processing version")
    ph["PROCDATE"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if image.source_path:
        ph["SRCFILE"] = Path(image.source_path).name[:68]
    if image.source_sha256:
        ph["SRCSHA"] = (image.source_sha256[:64], "sha256 of source cutout")
    ph["QUALFLAG"] = (",".join(image.quality_flags)[:68], "problem flags at grid centre")
    for k, v in (metadata or {}).items():
        key = str(k).upper()[:8]
        if isinstance(v, (str, int, float, bool)) and key not in ph:
            ph[key] = v if not isinstance(v, str) else v[:68]
    for h in image.history:
        ph.add_history(h[:72])

    mask_hdu = _image_hdu(image.mask, "MASK", w, dtype=np.uint8)
    for name, bit in ALL_MASK_BITS.items():
        mask_hdu.header[f"MASKB{bit}"] = (name, f"mask bit value {bit}")
    flags_hdu = _image_hdu(image.flags, "FLAGS", w, dtype=np.int32)
    for k, v, c in flag_definition_cards(image.flag_definitions):
        flags_hdu.header[k] = (v, c)
    hdus = [fits.PrimaryHDU(header=ph),
            _image_hdu(image.data, "SCI", w, "MJy / sr", np.float32)]
    if image.variance is not None:
        hdus.append(_image_hdu(image.variance, "VAR", w, "(MJy / sr)2", np.float32))
    hdus += [mask_hdu, flags_hdu, _image_hdu(image.raw, "RAW", w, "MJy / sr", np.float32)]
    if image.zodi_subtracted is not None:
        hdus.append(_image_hdu(image.zodi_subtracted, "ZSUB", w, "MJy / sr", np.float32))
    if image.interp_weight is not None:
        hdus.append(_image_hdu(image.interp_weight, "WGT", w, None, np.float32))
    if image.wavelength_map is not None:
        hdus.append(_image_hdu(image.wavelength_map, "WAVE", w, "um", np.float32))
    # Unique temp name: two requests processing the same field must not write the same file.
    tmp = output_path.with_name(output_path.name + f".{os.getpid()}.{threading.get_ident()}.part")
    _writeto(fits.HDUList(hdus), tmp)
    tmp.replace(output_path)
    image.fits_path = str(output_path)
    return output_path


def load_processed_observation(path: str | Path) -> ProcessedObservation:
    """Read back a file written by :func:`save_processed_observation`."""
    from backend.data_acquisition.fits_meta import decode_flag_definitions

    with fits.open(path, memmap=False) as h:
        ph = h[0].header
        names = [x.name for x in h]
        wcs = _quiet_wcs(h["SCI"].header)
        get = lambda n, dt=float: np.array(h[n].data, dtype=dt) if n in names else None  # noqa: E731
        return ProcessedObservation(
            observation_id=ph.get("OBS_ID"),
            observation_time=(ph.get("DATE-OBS") + "Z") if ph.get("DATE-OBS") else None,
            mjd=ph.get("MJD-OBS"), detector=ph.get("DETECTOR"), wavelength=ph.get("WAVELEN"),
            bandwidth=ph.get("BANDWID"), data=get("SCI"), variance=get("VAR"),
            mask=get("MASK", np.uint8), flags=get("FLAGS", np.int64), raw=get("RAW"),
            wavelength_map=get("WAVE"), wcs=wcs, zodi_subtracted=get("ZSUB"), interp_weight=get("WGT"),
            noise_correlation=ph.get("NOISECOR"), psf_fwhm=ph.get("PSF_FWHM"),
            background_level=ph.get("BKGLEVEL"), zodi_median=ph.get("ZODIMED"),
            quality_flags=[q for q in (ph.get("QUALFLAG") or "").split(",") if q],
            flag_definitions=decode_flag_definitions(h["FLAGS"].header),
            history=[str(x) for x in ph.get("HISTORY", [])],
            source_sha256=ph.get("SRCSHA"), data_release=ph.get("RELEASE"),
            processing={"mask_profile": ph.get("MASKPROF"), "background": ph.get("BKGMETH"),
                        "reproject_method": ph.get("REPROJ"), "pixel_scale_arcsec": ph.get("PIXSCALE"),
                        "version": ph.get("PROCVER"), "wavelength_source": ph.get("WAVESRC"),
                        "wavelength_calibration": ph.get("WAVECAL"), "pipeline_version": ph.get("PIPEVER"),
                        "psf_header_fix_needed": bool(ph.get("PSFFIX", False))},
            fits_path=str(path),
        )


# --------------------------------------------------------------------------
# URLs / output contract
# --------------------------------------------------------------------------
def file_url(path: str | Path | None) -> str | None:
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to(processed_root().resolve())
    except ValueError:
        return None
    return "/api/files/processed/" + rel.as_posix()


def product_dict(p: ProcessedObservation, display_limits: tuple[float, float] | None = None) -> dict[str, Any]:
    """The integration contract for one processed observation (+ extras)."""
    ch = p.channel
    return {
        "observation_id": p.observation_id,
        "observation_time": p.observation_time,
        "wavelength": p.wavelength,
        "image_url": file_url(p.png_path),
        "fits_url": file_url(p.fits_path),
        "wcs": wcs_to_dict(p.wcs),
        "quality_flags": p.quality_flags,
        "processing_method": (f"mask={p.processing.get('mask_profile')}; "
                              f"background={p.processing.get('background')}; "
                              f"reproject={p.processing.get('reproject_method')} "
                              f"@ {p.processing.get('pixel_scale_arcsec', 0):.2f}\"/px; "
                              f"v{PROCESSING_VERSION}"),
        # extras
        "mjd": p.mjd,
        "detector": p.detector,
        "bandwidth": p.bandwidth,
        "channel": None if ch is None else {"index": ch.index, "label": ch.label,
                                            "wavelength_min": ch.wavelength_min,
                                            "wavelength_max": ch.wavelength_max},
        "data_release": p.data_release,
        "valid_fraction": round(p.valid_fraction, 4),
        "background_level": p.background_level,
        "zodi_median": p.zodi_median,
        "psf_fwhm_arcsec": p.psf_fwhm,
        "noise_correlation": p.noise_correlation,
        "planes": ["SCI", "RAW"] + (["ZSUB"] if p.zodi_subtracted is not None else []),
        "masked_flags": p.masked_flags,
        "display_limits": list(display_limits) if display_limits else None,
        "units": "MJy/sr",
        "wavelength_source": p.processing.get("wavelength_source"),
        "wavelength_calibration": p.processing.get("wavelength_calibration"),
        "pipeline_version": p.processing.get("pipeline_version"),
        "history": p.history,
    }


# --------------------------------------------------------------------------
# Series processing
# --------------------------------------------------------------------------
@dataclass
class SeriesResult:
    grid: dict[str, Any]
    observations: list[ProcessedObservation]
    products: list[dict[str, Any]]
    series_dir: Path
    errors: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    animations: dict[str, str] = field(default_factory=dict)
    display_limits: dict[str, tuple[float, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"grid": self.grid, "count": len(self.products), "observations": self.products,
                "animations": {k: file_url(v) for k, v in self.animations.items()},
                "errors": self.errors, "skipped": self.skipped}


def limits_key(p: ProcessedObservation) -> str:
    """Display-limit group: the nominal wavelength channel, else the detector."""
    ch = p.channel
    return f"ch{ch.index:03d}" if ch is not None else f"D{p.detector}"


def series_key(ra: float, dec: float, fov: float, pixel_scale: float, mask_profile, background: str,
               method: str) -> str:
    return stable_hash({"ra": round(ra, 6), "dec": round(dec, 6), "fov": round(fov, 6),
                        "px": round(pixel_scale, 4), "mask": mask_profile, "bkg": background,
                        "m": method, "v": PROCESSING_VERSION})


def grid_info(wcs: WCS, shape: tuple[int, int], ra: float, dec: float, fov: float) -> dict[str, Any]:
    return {"center_ra": ra, "center_dec": dec, "fov_deg": fov, "shape": list(shape),
            "pixel_scale_arcsec": _pixel_scale_arcsec(wcs), "orientation": "north up, east left",
            "wcs": wcs_to_dict(wcs)}


def process_series(
    ra: float,
    dec: float,
    fov: float = 0.05,
    *,
    records: Sequence[ObservationRecord] | None = None,
    detectors: Iterable[int] | None = None,
    date_range: Any = None,
    release: Any = None,
    wavelength_range: Any = None,
    pixel_scale_arcsec: float = NATIVE_PIXEL_SCALE_ARCSEC,
    mask_profile: str | Iterable[str] = "standard",
    background: str = "zodi+median",
    method: str = "interp",
    stretch: str = "asinh",
    max_observations: int | None = 120,
    make_gifs: bool = True,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> SeriesResult:
    """Fetch, process and render every observation of a position on one grid.

    Uses the data acquisition module for searching and cutouts (cutouts are
    made large enough to cover the grid at any rotation). Products are
    cached: re-running with the same parameters reuses processed files whose
    source cutout has not changed.
    """
    s = get_settings()
    ref_wcs, shape = make_reference_wcs(ra, dec, fov, pixel_scale_arcsec)
    if records is None:
        records = search_observations(ra, dec, fov, date_range=date_range, release=release,
                                      wavelength_range=wavelength_range, detectors=detectors)
    elif detectors is not None:
        dets = set(int(d) for d in detectors)
        records = [r for r in records if r.detector in dets]
    records = list(records)
    skipped: list[dict[str, Any]] = []
    if max_observations is not None and len(records) > max_observations:
        skipped += [{"observation_id": r.observation_id, "reason": f"over max_observations={max_observations}"}
                    for r in records[max_observations:]]
        records = records[:max_observations]

    csize = min(cutout_size_for_grid(fov, pixel_scale_arcsec), s.max_fov_deg)
    batch = download_cutouts(records, ra, dec, csize, progress=progress)
    skipped += batch.skipped
    key = series_key(ra, dec, fov, pixel_scale_arcsec,
                     mask_profile if isinstance(mask_profile, str) else list(mask_profile), background, method)
    sdir = processed_root() / key
    errors = list(batch.errors)
    processed: list[ProcessedObservation] = []
    for rec in batch.records:
        out = sdir / f"{rec.observation_id}.fits"
        sha = (rec.cutout or {}).get("sha256")
        try:
            p = None
            if out.exists():
                cached = load_processed_observation(out)
                if sha and cached.source_sha256 == sha[:64]:
                    p = cached
                    p.png_path = str(out.with_suffix(".png"))
            if p is None:
                p = process_observation(rec.local_path, ref_wcs, shape, observation_id=rec.observation_id,
                                        observation_time=rec.observation_time, data_release=rec.data_release,
                                        mask_profile=mask_profile, background=background, method=method,
                                        source_sha256=sha)
                save_processed_observation(p, {"SRCURL": (rec.fits_url or "")[-68:]}, out)
            processed.append(p)
        except Exception as exc:  # one bad frame must not sink the series
            log.warning("processing failed for %s: %s", rec.observation_id, exc)
            errors.append({"observation_id": rec.observation_id, "error": f"processing: {exc}"})

    processed.sort(key=lambda p: (p.mjd or 0, p.detector or 0))
    # Shared display limits per wavelength channel: no flicker between frames at one wavelength,
    # and a bright frame at one wavelength cannot set the range of another.
    limits: dict[str, tuple[float, float]] = {}
    for k in sorted({limits_key(p) for p in processed}):
        limits[k] = compute_limits([p.data for p in processed if limits_key(p) == k], (1.0, 99.7))
    animations: dict[str, str] = {}
    for p in processed:
        lim = limits[limits_key(p)]
        png = sdir / f"{p.observation_id}.png"
        save_png(generate_display_image(p.data, stretch=stretch, vmin=lim[0], vmax=lim[1]), png)
        p.png_path = str(png)
    if make_gifs:
        for det in sorted({p.detector for p in processed}, key=lambda d: d or 0):
            frames = [p for p in processed if p.detector == det]
            if len(frames) >= 2:
                factor = max(1, 256 // max(shape))
                rgba = [upscale(generate_display_image(f.data, stretch=stretch, vmin=limits[limits_key(f)][0],
                                                       vmax=limits[limits_key(f)][1]), factor) for f in frames]
                animations[f"D{det}"] = str(make_animation(rgba, sdir / f"timelapse_D{det}.gif", 400))
    products = [product_dict(p, limits.get(limits_key(p))) for p in processed]
    return SeriesResult(grid=grid_info(ref_wcs, shape, ra, dec, fov), observations=processed,
                        products=products, series_dir=sdir, errors=errors, skipped=skipped,
                        animations=animations, display_limits=limits)


# --------------------------------------------------------------------------
# Grouping, selection, stacks
# --------------------------------------------------------------------------
def assign_epochs(observations: Sequence[ProcessedObservation], gap_days: float | None = None) -> dict[str, int]:
    """Epoch index per observation_id (a gap > ``gap_days`` between exposures starts a new epoch)."""
    gap = get_settings().epoch_gap_days if gap_days is None else gap_days
    obs = sorted((o for o in observations if o.mjd is not None), key=lambda o: o.mjd)
    out: dict[str, int] = {}
    epoch, last = -1, None
    for o in obs:
        if last is None or o.mjd - last > gap:
            epoch += 1
        out[o.observation_id] = epoch
        last = o.mjd
    return out


GROUP_BY = ("epoch", "date", "detector", "channel", "epoch_channel")


def group_observations(observations: Sequence[ProcessedObservation], group_by: str = "epoch"
                       ) -> dict[str, list[ProcessedObservation]]:
    """Group for stacking. Every grouping keeps detectors apart (different wavelength ranges)."""
    if group_by not in GROUP_BY:
        raise ValueError(f"group_by must be one of {GROUP_BY}")
    epochs = assign_epochs(observations)
    groups: dict[str, list[ProcessedObservation]] = {}
    for o in observations:
        ch = o.channel
        if group_by == "epoch":
            k = f"epoch{epochs.get(o.observation_id, 0)}_D{o.detector}"
        elif group_by == "date":
            k = f"{(o.observation_time or 'unknown')[:10]}_D{o.detector}"
        elif group_by == "detector":
            k = f"D{o.detector}"
        elif group_by == "channel":
            if ch is None:
                continue
            k = f"ch{ch.index:03d}"
        else:
            if ch is None:
                continue
            k = f"epoch{epochs.get(o.observation_id, 0)}_ch{ch.index:03d}"
        groups.setdefault(k, []).append(o)
    return dict(sorted(groups.items()))


def select_observations(observations: Sequence[ProcessedObservation], *, observation_ids=None,
                        epoch_index: int | None = None, date_start=None, date_end=None,
                        detector: int | None = None, wavelength_min: float | None = None,
                        wavelength_max: float | None = None) -> list[ProcessedObservation]:
    """Filter observations by IDs, epoch, date range, detector and/or wavelength."""
    sel = list(observations)
    if observation_ids:
        ids = set(observation_ids)
        sel = [o for o in sel if o.observation_id in ids]
    if epoch_index is not None:
        ep = assign_epochs(observations)
        sel = [o for o in sel if ep.get(o.observation_id) == epoch_index]
    if date_start is not None:
        t0 = Time(date_start).mjd if not isinstance(date_start, (int, float)) else float(date_start)
        sel = [o for o in sel if o.mjd is not None and o.mjd >= t0]
    if date_end is not None:
        t1 = Time(date_end).mjd if not isinstance(date_end, (int, float)) else float(date_end)
        sel = [o for o in sel if o.mjd is not None and o.mjd <= t1]
    if detector is not None:
        sel = [o for o in sel if o.detector == int(detector)]
    if wavelength_min is not None:
        sel = [o for o in sel if o.wavelength is not None and o.wavelength >= wavelength_min]
    if wavelength_max is not None:
        sel = [o for o in sel if o.wavelength is not None and o.wavelength <= wavelength_max]
    return sel


def _group_summary(members: Sequence[ProcessedObservation]) -> dict[str, Any]:
    wls = [m.wavelength for m in members if m.wavelength is not None]
    mjds = [m.mjd for m in members if m.mjd is not None]
    dets = sorted({m.detector for m in members if m.detector is not None})
    out = {
        "n_frames": len(members),
        "observation_ids": [m.observation_id for m in members],
        "detectors": dets,
        "time_start": min((m.observation_time for m in members if m.observation_time), default=None),
        "time_end": max((m.observation_time for m in members if m.observation_time), default=None),
        "mjd_mean": float(np.mean(mjds)) if mjds else None,
        "wavelength_mean": float(np.mean(wls)) if wls else None,
        "wavelength_min": float(min(wls)) if wls else None,
        "wavelength_max": float(max(wls)) if wls else None,
        "psf_fwhm_max": max((m.psf_fwhm for m in members if m.psf_fwhm), default=None),
        "warnings": [],
    }
    if len(dets) > 1:
        out["warnings"].append(f"mixes detectors {dets}: different wavelength ranges and PSFs")
    out["wavelength_compatible"] = True
    nc = [m.noise_correlation for m in members if m.noise_correlation]
    out["noise_correlation"] = float(np.mean(nc)) if nc else None
    if wls and dets and len(dets) == 1:
        width = channel_width_um(dets[0], float(np.median(wls)))
        if max(wls) - min(wls) > width:
            out["wavelength_compatible"] = False
            out["warnings"].append(
                f"members span {min(wls):.3f}-{max(wls):.3f} um (more than one ~{width:.3f} um channel): "
                "this combines different wavelengths, so flux reflects the source's spectrum as well as time")
    return out


def stack_group(members: Sequence[ProcessedObservation], method: str = "median", min_frames: int = 1,
                allow_mixed_detectors: bool = False):
    if not members:
        raise ValueError("Empty group")
    dets = {m.detector for m in members}
    if len(dets) > 1 and not allow_mixed_detectors:
        raise ValueError(f"Refusing to stack different detectors {sorted(d for d in dets if d)} "
                         "(different wavelengths/PSFs); pass allow_mixed_detectors=True to override.")
    shapes = {m.data.shape for m in members}
    if len(shapes) > 1:
        raise ValueError("Observations are on different grids.")
    variances = None
    if method == "weighted" or all(m.variance is not None for m in members):
        variances = [m.variance for m in members]
    res = stack_observations([m.data for m in members], method, variances, min_frames)
    return res, _group_summary(members)


WAVELENGTH_POLICIES = ("split", "warn")


def split_by_wavelength(groups: dict[str, list[ProcessedObservation]]) -> dict[str, list[ProcessedObservation]]:
    """Split any group whose members span more than one channel into one group per channel.

    SPHEREx's linear variable filters put a source at a different wavelength
    in each exposure, so "same sky, same epoch" is not "same measurement".
    Keys of split groups get a ``_chNNN`` suffix.
    """
    out: dict[str, list[ProcessedObservation]] = {}
    for k, mem in groups.items():
        if _group_summary(mem).get("wavelength_compatible", True):
            out[k] = mem
            continue
        for m in mem:
            ch = m.channel
            out.setdefault(f"{k}_ch{ch.index:03d}" if ch is not None else f"{k}_chNA", []).append(m)
    return dict(sorted(out.items()))


def make_stacks(observations: Sequence[ProcessedObservation], group_by: str = "epoch",
                method: str = "median", min_frames: int = 1, output_dir: str | Path | None = None,
                stretch: str = "asinh", wavelength_policy: str = "split") -> list[dict[str, Any]]:
    """Stack each group and write ``stack_<key>.fits`` / ``.png``. Returns product dicts.

    ``wavelength_policy="split"`` (default) stacks only frames at compatible
    wavelengths (one nominal channel), splitting mixed groups by channel;
    ``"warn"`` keeps the group and records a warning.
    """
    if method not in STACK_METHODS:
        raise ValueError(f"method must be one of {STACK_METHODS}")
    if wavelength_policy not in WAVELENGTH_POLICIES:
        raise ValueError(f"wavelength_policy must be one of {WAVELENGTH_POLICIES}")
    if not observations:
        return []
    out_dir = Path(output_dir) if output_dir else processed_root() / "stacks"
    wcs = observations[0].wcs
    products = []
    groups = group_observations(observations, group_by)
    if wavelength_policy == "split":
        groups = split_by_wavelength(groups)
    stacks = {k: stack_group(v, method, min_frames) for k, v in groups.items()}

    # shared limits per wavelength channel (detector when the stack spans several channels)
    def lkey(summ):
        dets = summ["detectors"]
        if summ.get("wavelength_compatible") and summ["wavelength_mean"] is not None and len(dets) == 1:
            ch = channel_for(dets[0], summ["wavelength_mean"])
            if ch is not None:
                return f"ch{ch.index:03d}"
        return "D" + "".join(str(d) for d in dets)
    lims: dict[str, tuple[float, float]] = {}
    for dk in {lkey(s) for _, s in stacks.values()}:
        lims[dk] = compute_limits([r.data for r, s in stacks.values() if lkey(s) == dk], (1.0, 99.7))
    for k, (res, summ) in stacks.items():
        name = f"stack_{group_by}_{method}_{k}"
        path = out_dir / f"{name}.fits"
        h = wcs.to_header()
        ph = fits.Header()
        ph["STACKMET"] = method
        ph["GROUPBY"] = group_by
        ph["GROUPKEY"] = k
        ph["NFRAMES"] = summ["n_frames"]
        if summ["wavelength_mean"] is not None:
            ph["WAVELEN"] = (round(summ["wavelength_mean"], 5), "[um] mean wavelength of members")
        ph["BUNIT"] = "MJy / sr"
        ph["PROCVER"] = PROCESSING_VERSION
        if summ.get("noise_correlation"):
            ph["NOISECOR"] = (round(summ["noise_correlation"], 4), "VAR x this = variance of large-area sums")
        for oid in summ["observation_ids"]:
            ph.add_history(f"member {oid}")
        for w in summ["warnings"]:
            ph.add_comment(w[:72])
        hdus = [fits.PrimaryHDU(header=ph), _image_hdu(res.data, "SCI", h, "MJy / sr", np.float32)]
        if res.variance is not None:
            hdus.append(_image_hdu(res.variance, "VAR", h, "(MJy / sr)2", np.float32))
        hdus.append(_image_hdu(res.n_used, "NFRAMES", h, None, np.int16))
        path.parent.mkdir(parents=True, exist_ok=True)
        _writeto(fits.HDUList(hdus), path)
        lim = lims[lkey(summ)]
        png = save_png(generate_display_image(res.data, stretch=stretch, vmin=lim[0], vmax=lim[1]),
                       path.with_suffix(".png"))
        products.append({"key": k, "group_by": group_by, "method": method, **summ,
                         "image_url": file_url(png), "fits_url": file_url(path),
                         "valid_fraction": round(float(res.valid.mean()), 4),
                         "display_limits": list(lim), "wcs": wcs_to_dict(wcs)})
    return products


# --------------------------------------------------------------------------
# Comparisons
# --------------------------------------------------------------------------
def match_wavelengths(a: Sequence[ProcessedObservation], b: Sequence[ProcessedObservation],
                      tolerance_um: float | None = None) -> tuple[list, list, list[tuple[str, str, float]]]:
    """Keep only frames of A and B that have a counterpart at (nearly) the same wavelength.

    Default tolerance: one nominal channel width. Returns (a', b', pairs).
    """
    pairs = []
    for x in a:
        if x.wavelength is None:
            continue
        cands = [y for y in b if y.wavelength is not None and y.detector == x.detector]
        if not cands:
            continue
        y = min(cands, key=lambda y: abs(y.wavelength - x.wavelength))
        tol = tolerance_um if tolerance_um is not None else channel_width_um(x.detector, x.wavelength)
        d = abs(y.wavelength - x.wavelength)
        if d <= tol:
            pairs.append((x.observation_id, y.observation_id, d))
    ida = {p[0] for p in pairs}
    idb = {p[1] for p in pairs}
    return [x for x in a if x.observation_id in ida], [y for y in b if y.observation_id in idb], pairs


def _combine_side(members: Sequence[ProcessedObservation], method: str):
    if len(members) == 1:
        m = members[0]
        return m.data, m.variance, m.psf_fwhm
    res, summ = stack_group(members, method)
    return res.data, res.variance, summ["psf_fwhm_max"]


def compare(a: Sequence[ProcessedObservation], b: Sequence[ProcessedObservation], mode: str = "difference",
            *, stack_method: str = "median", psf_matching: bool = True, match_background: bool = True,
            match_wavelength: bool = False, wavelength_tolerance_um: float | None = None,
            min_snr_b: float = 5.0, allow_mixed_detectors: bool = False,
            smooth_fwhm_arcsec: float = 0.0,
            output_dir: str | Path | None = None, name: str | None = None) -> dict[str, Any]:
    """Compare two sets of aligned observations (each set is stacked if > 1 frame).

    ``mode``: ``"difference"`` (A - B) or ``"ratio"`` (A / B).

    ``smooth_fwhm_arcsec`` blurs both sides with a Gaussian of that FWHM
    before comparing. SPHEREx's PSF (FWHM ~7.5-9") is undersampled by its
    6.15" pixels, so a star's pixel values depend on where it falls within
    a pixel; single-frame differences of bright stationary stars leave
    residuals of ~10-40% of the peak. Smoothing to 15" cuts this to
    ~5-12% but also blurs small motions; stacking several frames per side
    averages the effect out.
    Writes ``<name>.fits`` (DATA, VAR, SIG, MASK) and PNGs, and returns a
    product dict with statistics and automatically generated caveats.
    """
    if mode not in ("difference", "ratio"):
        raise ValueError("mode must be 'difference' or 'ratio'")
    a, b = list(a), list(b)
    if not a or not b:
        raise ValueError("Both A and B need at least one observation.")
    pairs = None
    if match_wavelength:
        a, b, pairs = match_wavelengths(a, b, wavelength_tolerance_um)
        if not a:
            raise ValueError("No wavelength-matched frames between A and B; widen the tolerance "
                             "or choose other observations.")
    dets = {o.detector for o in a + b}
    if len(dets) > 1 and not allow_mixed_detectors:
        raise ValueError(f"A and B use different detectors {sorted(d for d in dets if d)}: their wavelengths "
                         "differ, so differences would be spectral, not temporal. "
                         "Pass allow_mixed_detectors=True to override.")
    da, va, fa = _combine_side(a, stack_method)
    db, vb, fb = _combine_side(b, stack_method)
    hist = [f"A: {len(a)} frame(s), B: {len(b)} frame(s), stacked with {stack_method}"]
    if psf_matching:
        scale = _pixel_scale_arcsec(a[0].wcs)
        da, va, db, vb, h = psf_match(da, fa, db, fb, scale, va, vb)
        hist += h
    if smooth_fwhm_arcsec and smooth_fwhm_arcsec > 0:
        from .combine import convolve_nan

        s_px = smooth_fwhm_arcsec / 2.3548 / _pixel_scale_arcsec(a[0].wcs)
        da, va = convolve_nan(da, s_px, va)
        db, vb = convolve_nan(db, s_px, vb)
        hist.append(f"smoothed A and B with a {smooth_fwhm_arcsec:.1f}\" FWHM Gaussian")
    if mode == "difference":
        res: ComparisonResult = subtract_images(da, db, va, vb, match_background=match_background)
    else:
        res = divide_images(da, db, va, vb, min_snr_b=min_snr_b)
    hist += res.history

    sa, sb = _group_summary(a), _group_summary(b)
    caveats = []
    if sa["wavelength_mean"] and sb["wavelength_mean"]:
        dl = abs(sa["wavelength_mean"] - sb["wavelength_mean"])
        det = next(iter(dets)) if len(dets) == 1 else None
        width = channel_width_um(det, sa["wavelength_mean"]) if det else float("nan")
        if det and dl > width:
            caveats.append(f"A and B were observed at different wavelengths ({sa['wavelength_mean']:.3f} vs "
                           f"{sb['wavelength_mean']:.3f} um, > 1 channel): flux differences may be spectral, "
                           "not changes in time. Use match_wavelength=True for a like-for-like comparison.")
    caveats += [f"A: {w}" for w in sa["warnings"]] + [f"B: {w}" for w in sb["warnings"]]
    caveats.append("Significance assumes A and B have independent errors; resampling correlates neighbouring "
                   "pixels, so sums over several pixels are noisier than the per-pixel sigma suggests.")
    caveats.append("Residuals can come from PSF differences, background, calibration or noise; "
                   "a residual is a candidate for inspection, not a detected moving object.")
    if (len(a) == 1 or len(b) == 1) and not smooth_fwhm_arcsec:
        caveats.append("Single-frame comparison: SPHEREx's PSF is undersampled, so even stationary bright "
                       "stars can leave residuals of 10-40% of their peak. Stack several frames per side "
                       "or set smooth_fwhm_arcsec to reduce this.")
    if any(o.quality_flags for o in a + b):
        caveats.append("Some frames have problem flags at the target (e.g. saturation); see quality_flags.")

    stats: dict[str, Any] = {"valid_fraction": round(float(res.valid.mean()), 4)}
    if res.significance is not None and np.isfinite(res.significance).sum() >= 5:
        sig = res.significance[np.isfinite(res.significance)]
        stats.update({"significance_robust_std": float(mad_std(sig)),
                      "n_pixels_abs_sig_gt_5": int((np.abs(sig) > 5).sum()),
                      "max_abs_significance": float(np.abs(sig).max())})
        if stats["significance_robust_std"] > 1.5:
            caveats.append(f"Noise in the comparison is {stats['significance_robust_std']:.1f}x the propagated "
                           "uncertainty (interpolation/PSF/background effects): treat significances as upper bounds.")
    if sa["mjd_mean"] and sb["mjd_mean"]:
        stats["delta_t_days"] = round(sa["mjd_mean"] - sb["mjd_mean"], 3)

    out_dir = Path(output_dir) if output_dir else processed_root() / "compare"
    name = name or ("cmp_" + stable_hash({"a": sa["observation_ids"], "b": sb["observation_ids"], "m": mode,
                                          "s": stack_method, "p": psf_matching, "bg": match_background,
                                          "w": match_wavelength, "v": PROCESSING_VERSION}))
    wcs = a[0].wcs
    h = wcs.to_header()
    ph = fits.Header()
    ph["CMPMODE"] = mode
    ph["NFRAMESA"], ph["NFRAMESB"] = len(a), len(b)
    if "delta_t_days" in stats:
        ph["DELTAT"] = (stats["delta_t_days"], "[d] mean time A - B")
    ph["PROCVER"] = PROCESSING_VERSION
    for x in hist:
        ph.add_history(x[:72])
    for x in sa["observation_ids"]:
        ph.add_history(f"A member {x}")
    for x in sb["observation_ids"]:
        ph.add_history(f"B member {x}")
    mask = np.where(res.valid, 0, 1).astype(np.uint8)
    hdus = [fits.PrimaryHDU(header=ph),
            _image_hdu(res.data, "DIFF" if mode == "difference" else "RATIO", h,
                       "MJy / sr" if mode == "difference" else None, np.float32)]
    if res.variance is not None:
        hdus.append(_image_hdu(res.variance, "VAR", h, None, np.float32))
    if res.significance is not None:
        hdus.append(_image_hdu(res.significance, "SIG", h, "sigma", np.float32))
    hdus.append(_image_hdu(mask, "MASK", h, None, np.uint8))
    path = out_dir / f"{name}.fits"
    path.parent.mkdir(parents=True, exist_ok=True)
    _writeto(fits.HDUList(hdus), path)

    shown = res.data if mode == "difference" else res.data - 1.0
    img_png = save_png(generate_display_image(shown, diverging=True), out_dir / f"{name}.png")
    sig_png = None
    if res.significance is not None:
        sig_png = save_png(generate_display_image(res.significance, diverging=True, vmin=-5, vmax=5),
                           out_dir / f"{name}_sig.png")
    return {
        "mode": mode, "name": name,
        "image_url": file_url(img_png), "significance_url": file_url(sig_png), "fits_url": file_url(path),
        "a": sa, "b": sb, "wavelength_pairs": pairs, "stats": stats, "caveats": caveats,
        "history": hist, "wcs": wcs_to_dict(wcs),
        "display": ("diverging blue-white-red: red = brighter in A, blue = brighter in B"
                    if mode == "difference" else "diverging around ratio 1: red = A/B > 1"),
    }
