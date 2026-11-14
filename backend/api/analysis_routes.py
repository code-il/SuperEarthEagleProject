"""Analysis routes (Workstream 4): sources, motion, light curves, spectra, classes.

Field level
    ``GET  /api/analysis``          what the viewer calls: overlays ``{sources, light_curves, spectra}``,
                                    ``results_summary`` (one compact record per source), field summary,
                                    warnings and provenance; ``full=true`` adds the complete records.
    ``POST /api/analysis/detect``   the same, with the field in a JSON body.

Source level (source ids such as ``S001`` are local to a field, so the field is
given again as query parameters; the field's analysis is cached, so these are cheap
once the field has been analysed)
    ``GET /api/analysis/{source_id}``   the full record (detections with times and errors,
                                        motion, variability, classification criteria, catalogue match)
    ``GET /api/light-curve/{source_id}``
    ``GET /api/spectrum/{source_id}``   per-visit spectra, combined profile, visit-to-visit ratio

``GET /api/analysis/crossmatch`` looks one position up in Gaia DR3 / CatWISE / SIMBAD (/ SkyBoT).

Processing is delegated to Person 2's ``process_series`` with the viewer's
default options, so a field the viewer has opened is analysed from its
cached processed frames.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from backend.analysis import (ANALYSIS_VERSION, analyse_field, compare_spectra, crossmatch_catalogue, find_source,
                              summarise_source, to_viewer)
from backend.analysis.crossmatch import DEFAULT_CATALOGS
from backend.analysis.pipeline import DISCLAIMER
from backend.api.data_routes import _http_error, _position
from backend.data_acquisition.coords import CoordinateError, resolve_name

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["analysis"])

_RESULTS: "OrderedDict[tuple, tuple[float, dict[str, Any]]]" = OrderedDict()
_RESULTS_LOCK = threading.Lock()
_RESULTS_MAX = 24
_RESULTS_TTL_S = 3600.0


def _detectors(det: str | None) -> list[int]:
    if not det:
        return [6]
    out = []
    for p in det.replace(" ", "").split(","):
        p = p.upper().lstrip("D")
        if not p.isdigit() or not 1 <= int(p) <= 6:
            raise CoordinateError(f"Bad detector {p!r}; use 1-6, e.g. det=6 or det=4,5,6")
        out.append(int(p))
    return sorted(set(out))


def _catalogs(catalogs: str | None) -> tuple[str, ...]:
    cats = DEFAULT_CATALOGS if catalogs is None else tuple(
        c for c in catalogs.lower().replace(" ", "").split(",") if c and c != "none")
    bad = [c for c in cats if c not in DEFAULT_CATALOGS]
    if bad:
        raise CoordinateError(f"Unknown catalogue(s) {bad}; use {list(DEFAULT_CATALOGS)}")
    return cats


def _run(q: str | None, ra: Any, dec: Any, fov: float, det: str | None, target: str | None,
         catalogs: str | None, skybot: bool, refresh: bool) -> dict[str, Any]:
    """Analyse one field (memoised in-process, and on disk by ``analyse_field``)."""
    if not 0 < fov <= 0.5:
        raise CoordinateError("fov must be in (0, 0.5] deg for the analysis")
    pos = _position(ra, dec, q, None)
    dets, cats = _detectors(det), _catalogs(catalogs)
    name = target or (q if pos.get("kind") == "name" else None)
    key = (round(pos["ra"], 6), round(pos["dec"], 6), round(fov, 5), tuple(dets), name, cats, skybot)
    now = time.time()
    with _RESULTS_LOCK:
        hit = _RESULTS.get(key)
        if hit and not refresh and now - hit[0] < _RESULTS_TTL_S:
            _RESULTS.move_to_end(key)
            return hit[1]
    tgt = None
    if name:
        try:
            tgt = resolve_name(name)
        except Exception as exc:  # the analysis still runs without the ephemeris check
            log.info("target %r not resolved: %s", name, exc)
    res = analyse_field(pos["ra"], pos["dec"], fov, target=tgt, detectors=dets,
                        use_cache=not refresh, catalogs=cats, use_skybot=skybot)
    with _RESULTS_LOCK:
        _RESULTS[key] = (now, res)
        while len(_RESULTS) > _RESULTS_MAX:
            _RESULTS.popitem(last=False)
    return res


def _field_response(res: dict[str, Any], full: bool) -> dict[str, Any]:
    out = {**to_viewer(res), "analysis_version": ANALYSIS_VERSION, "field": res.get("field"),
           "summary": res.get("summary"), "target": res.get("target"), "warnings": res.get("warnings", []),
           "disclaimer": DISCLAIMER, "catalogues": res.get("catalogues"),
           "registration_calibration": res.get("registration_calibration"), "visits": res.get("visits"),
           "provenance": res.get("provenance"),
           "results_summary": [summarise_source(s) for s in res.get("sources", [])]}
    if full:
        out["results"] = res.get("sources", [])
    return out


_FOV = Query(0.05, gt=0, le=0.5, description="Field of view (deg), as in the viewer")
_DET = Query(None, description="Detectors, e.g. 6 or 4,5,6 (default 6, as in the viewer)")
_TGT = Query(None, description="Name of the object of interest, to compare it with its ephemeris")
_CATS = Query(None, description="Comma list of gaia,catwise,simbad (default all); 'none' to skip")


@router.get("/analysis")
def analysis(q: str | None = None, ra: str | None = None, dec: str | None = None, fov: float = _FOV,
             det: str | None = _DET, target: str | None = _TGT, catalogs: str | None = _CATS,
             skybot: bool = Query(True, description="Check fast-mover candidates against SkyBoT (known asteroids/comets)"),
             refresh: bool = Query(False, description="Ignore the cached analysis"),
             full: bool = Query(True, description="Include complete per-source records (`results`)")):
    """Detect, track and classify sources in a field's SPHEREx time series.

    Returns the viewer overlays ``{sources, light_curves, spectra}``,
    ``results_summary`` (per source: class, confidence, status, motion,
    variability, catalogue match, criteria), ``summary``, ``target``,
    ``visits``, ``warnings``, ``provenance``; with ``full`` also ``results``
    (complete records with every detection's time, position and uncertainty).
    """
    try:
        return _field_response(_run(q, ra, dec, fov, det, target, catalogs, skybot, refresh), full)
    except Exception as exc:
        raise _http_error(exc)


class DetectRequest(BaseModel):
    q: str | None = Field(None, description="Object name or coordinates")
    ra: str | float | None = None
    dec: str | float | None = None
    fov: float = Field(0.05, gt=0, le=0.5)
    detectors: list[int] | None = Field(None, description="Default [6]")
    target: str | None = None
    catalogs: list[str] | None = None
    skybot: bool = True
    refresh: bool = False
    full: bool = False


@router.post("/analysis/detect")
def analysis_detect(req: DetectRequest):
    """Run (or fetch the cached) analysis of a field given as a JSON body; same response as ``GET /api/analysis``."""
    try:
        det = ",".join(str(d) for d in req.detectors) if req.detectors else None
        cats = None if req.catalogs is None else (",".join(req.catalogs) or "none")
        res = _run(req.q, req.ra, req.dec, req.fov, det, req.target, cats, req.skybot, req.refresh)
        return _field_response(res, req.full)
    except Exception as exc:
        raise _http_error(exc)


@router.get("/analysis/crossmatch")
def crossmatch(q: str | None = None, ra: str | None = None, dec: str | None = None,
               radius: float = Query(4.0, gt=0, le=600, description="Match radius in arcsec"),
               epoch: str | None = Query(None, description="Propagate catalogue positions to this date"),
               solar_system: bool = Query(False, description="Also query SkyBoT (needs epoch)")):
    """Look a position up in Gaia DR3, CatWISE2020 and SIMBAD (and SkyBoT)."""
    try:
        pos = _position(ra, dec, q, epoch)
        return crossmatch_catalogue(pos["ra"], pos["dec"], radius / 3600, epoch=epoch,
                                    include_solar_system=solar_system)
    except Exception as exc:
        raise _http_error(exc)


def _source(source_id: str, q, ra, dec, fov, det, target, catalogs) -> tuple[dict[str, Any], dict[str, Any]]:
    res = _run(q, ra, dec, fov, det, target, catalogs, True, False)
    return res, find_source(res, source_id)


@router.get("/analysis/{source_id}")
def analysis_source(source_id: str, q: str | None = None, ra: str | None = None, dec: str | None = None,
                    fov: float = _FOV, det: str | None = _DET, target: str | None = _TGT, catalogs: str | None = _CATS):
    """Complete record of one source of a field (ids from ``GET /api/analysis``)."""
    try:
        res, s = _source(source_id, q, ra, dec, fov, det, target, catalogs)
        return {**s, "summary": summarise_source(s), "field": res.get("field"),
                "disclaimer": DISCLAIMER, "analysis_version": ANALYSIS_VERSION}
    except Exception as exc:
        raise _http_error(exc)


@router.get("/light-curve/{source_id}")
def light_curve(source_id: str, q: str | None = None, ra: str | None = None, dec: str | None = None,
                fov: float = _FOV, det: str | None = _DET, target: str | None = _TGT, catalogs: str | None = _CATS):
    """Flux (mJy, AB mag) vs time of one source, with the variability test and its caveats."""
    try:
        _, s = _source(source_id, q, ra, dec, fov, det, target, catalogs)
        return {"source_id": s["source_id"], "label": s["label"], "saturated": s.get("saturated", False),
                "light_curve": s.get("light_curve") or {"points": []},
                "variability_indicator": s.get("variability_indicator")}
    except Exception as exc:
        raise _http_error(exc)


@router.get("/spectrum/{source_id}")
def spectrum(source_id: str, q: str | None = None, ra: str | None = None, dec: str | None = None,
             fov: float = _FOV, det: str | None = _DET, target: str | None = _TGT, catalogs: str | None = _CATS,
             compare_to: str | None = Query(None, description="Another source id: ratio of the two combined spectra")):
    """Per-visit and combined multi-channel spectrum of one source.

    ``visit_ratios`` compares each later visit with the first on shared
    channels; ``compare_to`` adds the ratio to another source of the field.
    """
    try:
        res, s = _source(source_id, q, ra, dec, fov, det, target, catalogs)
        sp = s.get("spectrum") or {"visits": [], "combined": []}
        visits = [v for v in sp.get("visits", []) if v.get("points")]
        ratios = []
        if len(visits) > 1:
            # A visit's own points binned to the combined spectrum's channels, so visits compare on shared channels.
            bins = [(c["channel"], c["wavelength_min"], c["wavelength_max"]) for c in sp.get("combined", [])
                    if c.get("wavelength_min") is not None and c.get("wavelength_max") is not None]

            def _binned(v):
                by: dict[int, list] = {}
                for p in v["points"]:
                    if p.get("flux") is None:
                        continue
                    ch = next((c for c, lo, hi in bins if lo <= p["wavelength"] <= hi), None)
                    if ch is not None:
                        by.setdefault(ch, []).append(p)
                return {"source_id": f"visit {v['epoch'] + 1}", "combined": [
                    {"channel": ch, "wavelength": sum(p["wavelength"] for p in ps) / len(ps),
                     "flux": sum(p["flux"] for p in ps) / len(ps),
                     "flux_err": (sum((p.get("flux_err") or 0) ** 2 for p in ps) ** 0.5) / len(ps)}
                    for ch, ps in sorted(by.items())]}
            first = _binned(visits[0])
            ratios = [compare_spectra(_binned(v), first) for v in visits[1:]]
        out = {"source_id": s["source_id"], "label": s["label"], "saturated": s.get("saturated", False),
               "spectrum": sp, "visit_ratios": ratios}
        if compare_to:
            other = find_source(res, compare_to)
            out["comparison"] = compare_spectra(sp, other.get("spectrum") or {})
        return out
    except Exception as exc:
        raise _http_error(exc)
