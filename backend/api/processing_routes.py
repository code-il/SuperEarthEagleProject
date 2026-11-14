"""FastAPI routes for the image processing engine (Workstream 2).

Provisional until agreed with the integration lead (Person 5). Positions
are given as in the data routes (``q`` or ``ra``/``dec``).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.api.data_routes import SearchRequest, _http_error, _position, _range
from backend.image_processing import (MASK_PROFILES, all_channels, compare, make_stacks,
                                      process_series, select_observations)
from backend.image_processing.align import NATIVE_PIXEL_SCALE_ARCSEC, REPROJECT_METHODS
from backend.image_processing.combine import STACK_METHODS
from backend.image_processing.display import STRETCHES
from backend.image_processing.pipeline import GROUP_BY, processed_root
from backend.image_processing.prepare import BACKGROUND_METHODS

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["image processing"])


class ProcessingOptions(BaseModel):
    pixel_scale_arcsec: float = Field(NATIVE_PIXEL_SCALE_ARCSEC, gt=0.5, le=30,
                                      description="Output grid scale; 6.15 = native SPHEREx pixels.")
    mask_profile: str | list[str] = Field("standard", description="none | minimal | standard | strict | [flag names]")
    background: str = Field("zodi+median", description="none | zodi | median | zodi+median")
    reproject_method: str = Field("interp", description="interp | exact | nearest")
    stretch: str = Field("asinh", description="Display stretch for PNGs: linear | sqrt | log | asinh")
    max_observations: int = Field(120, ge=1, le=400)


class ProcessRequest(SearchRequest, ProcessingOptions):
    stack_by: str | None = Field(None, description="Also build stacks: epoch | date | detector | channel | epoch_channel")
    stack_method: str = Field("median", description="mean | median | weighted")
    make_gifs: bool = True


class Selector(BaseModel):
    observation_ids: list[str] | None = None
    epoch_index: int | None = None
    date_start: str | None = None
    date_end: str | None = None
    wavelength_min: float | None = None
    wavelength_max: float | None = None


class CompareRequest(SearchRequest, ProcessingOptions):
    a: Selector = Field(..., description="Observations for image A (stacked if several).")
    b: Selector = Field(..., description="Observations for image B.")
    mode: str = Field("difference", description="difference | ratio")
    stack_method: str = "median"
    psf_matching: bool = True
    match_background: bool = True
    match_wavelength: bool = Field(False, description="Keep only A/B frames observed at matching wavelengths.")
    wavelength_tolerance_um: float | None = None
    min_snr_b: float = Field(5.0, description="Ratio mode: B must reach this S/N to divide.")
    allow_mixed_detectors: bool = False
    smooth_fwhm_arcsec: float = Field(0.0, ge=0, le=60,
                                      description="Blur both sides (Gaussian FWHM) to suppress undersampling residuals.")


def _validate_options(o: ProcessingOptions) -> None:
    if isinstance(o.mask_profile, str) and o.mask_profile not in MASK_PROFILES:
        raise ValueError(f"mask_profile must be one of {sorted(MASK_PROFILES)} or a list of flag names")
    if o.background not in BACKGROUND_METHODS:
        raise ValueError(f"background must be one of {BACKGROUND_METHODS}")
    if o.reproject_method not in REPROJECT_METHODS:
        raise ValueError(f"reproject_method must be one of {REPROJECT_METHODS}")
    if o.stretch not in STRETCHES:
        raise ValueError(f"stretch must be one of {STRETCHES}")


def _series(req: SearchRequest | Any) -> tuple[dict[str, Any], Any]:
    _validate_options(req)
    pos = _position(req.ra, req.dec, req.q, req.epoch)
    series = process_series(
        pos["ra"], pos["dec"], req.fov,
        detectors=req.detectors, date_range=_range(req.date_start, req.date_end), release=req.release,
        wavelength_range=_range(req.wavelength_min, req.wavelength_max),
        pixel_scale_arcsec=req.pixel_scale_arcsec, mask_profile=req.mask_profile,
        background=req.background, method=req.reproject_method, stretch=req.stretch,
        max_observations=req.max_observations, make_gifs=getattr(req, "make_gifs", False),
    )
    return pos, series


@router.get("/processing/options")
def options():
    """Valid values for processing parameters (for building UI controls)."""
    return {"mask_profiles": {k: v for k, v in MASK_PROFILES.items()}, "background": list(BACKGROUND_METHODS),
            "reproject_methods": list(REPROJECT_METHODS), "stretches": list(STRETCHES),
            "stack_methods": list(STACK_METHODS), "group_by": list(GROUP_BY),
            "native_pixel_scale_arcsec": NATIVE_PIXEL_SCALE_ARCSEC}


@router.get("/channels")
def channels():
    """The 102 nominal SPHEREx channels (17 per detector, log-spaced)."""
    return {"channels": [{"index": c.index, "detector": c.detector, "local_index": c.local_index,
                          "wavelength_min": c.wavelength_min, "wavelength_max": c.wavelength_max,
                          "wavelength_center": c.wavelength_center, "label": c.label}
                         for c in all_channels()]}


@router.post("/process")
def process(req: ProcessRequest):
    """Align every observation of a position onto one north-up grid; return per-frame products.

    Each observation: processed FITS (SCI/VAR/MASK/FLAGS/RAW/WAVE) and a PNG
    with display limits shared per detector. Optional stacks.
    Tip: restrict ``detectors`` (e.g. [6]); all six detectors means ~6x more downloads.
    """
    try:
        if req.stack_by is not None and req.stack_by not in GROUP_BY:
            raise ValueError(f"stack_by must be one of {GROUP_BY}")
        pos, series = _series(req)
        out = series.to_dict()
        out["query"] = pos
        if req.stack_by:
            out["stacks"] = make_stacks(series.observations, req.stack_by, req.stack_method,
                                        output_dir=series.series_dir / "stacks", stretch=req.stretch)
        return out
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc)


@router.post("/compare")
def compare_route(req: CompareRequest):
    """Difference (A - B) or ratio (A / B) of two observation sets on the common grid."""
    try:
        if req.mode not in ("difference", "ratio"):
            raise ValueError("mode must be 'difference' or 'ratio'")
        pos, series = _series(req)
        obs = series.observations
        a = select_observations(obs, **req.a.model_dump())
        b = select_observations(obs, **req.b.model_dump())
        if not a or not b:
            raise ValueError(f"Selection is empty (A: {len(a)}, B: {len(b)} of {len(obs)} processed observations).")
        out = compare(a, b, req.mode, stack_method=req.stack_method, psf_matching=req.psf_matching,
                      match_background=req.match_background, match_wavelength=req.match_wavelength,
                      wavelength_tolerance_um=req.wavelength_tolerance_um, min_snr_b=req.min_snr_b,
                      allow_mixed_detectors=req.allow_mixed_detectors,
                      smooth_fwhm_arcsec=req.smooth_fwhm_arcsec,
                      output_dir=series.series_dir / "compare")
        out["query"] = pos
        out["grid"] = series.grid
        return out
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc)


_MEDIA = {".fits": "application/fits", ".png": "image/png", ".gif": "image/gif", ".json": "application/json"}


@router.get("/files/processed/{file_path:path}")
def processed_file(file_path: str):
    root = processed_root().resolve()
    target = (root / file_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(404, "Not found")
    if not target.is_file() or target.suffix not in _MEDIA:
        raise HTTPException(404, "Not found")
    return FileResponse(target, media_type=_MEDIA[target.suffix], filename=target.name)
