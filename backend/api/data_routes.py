"""FastAPI routes for the data acquisition engine.

Route names follow the structure suggested for the integration workstream
(``/api/observations``, ``/api/search``, ``/api/cutouts`` ...). They are
provisional until agreed with the integration lead (Person 5).

All positions can be given either as ``ra``/``dec`` (decimal degrees or
sexagesimal, RA in hours) or as ``q`` (an object name or a coordinate
string). Named targets are propagated with their proper motion to
``SPHEREX_REFERENCE_EPOCH`` before searching.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.data_acquisition import (MANDATORY_TARGETS, ArchiveError, CoordinateError,
                                      DownloadError, HttpError, ObservationRecord,
                                      download_cutouts, get_available_bands,
                                      get_available_observation_dates, get_observation_metadata,
                                      get_settings, predicted_positions, resolve_target,
                                      search_observations)
from backend.data_acquisition.archive import group_epochs
from backend.data_acquisition.coords import ResolvedTarget

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["data acquisition"])


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CoordinateError) or (isinstance(exc, ValueError) and not isinstance(exc, KeyError)):
        return HTTPException(400, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(404, detail=str(exc).strip("'\""))
    if isinstance(exc, (ArchiveError, HttpError, DownloadError)):
        return HTTPException(502, detail=f"Upstream archive error: {exc}")
    log.exception("unexpected error")
    # Do not echo exception text (it can contain server file paths); details are in the server log.
    return HTTPException(500, detail=f"Internal server error ({type(exc).__name__}). Please retry; details are in the server log.")


def _position(ra: Any, dec: Any, q: str | None, epoch: str | None) -> dict[str, Any]:
    if q:
        return resolve_target(q, epoch=epoch or get_settings().reference_epoch)
    if ra is None or dec is None:
        raise CoordinateError("Provide either 'q' (name or coordinates) or both 'ra' and 'dec'.")
    from backend.data_acquisition import parse_coordinates

    c = parse_coordinates(ra, dec)
    return {"query": f"{ra} {dec}", "ra": float(c.ra.deg), "dec": float(c.dec.deg),
            "kind": "coordinates", "name": None, "target": None, "epoch": None}


def _file_url(local_path: str | None) -> str | None:
    if not local_path:
        return None
    root = get_settings().cutout_dir.resolve()
    try:
        rel = Path(local_path).resolve().relative_to(root)
    except ValueError:
        return None
    return "/api/files/cutouts/" + rel.as_posix()


def _record_out(rec: ObservationRecord, target_pos: tuple[float, float] | None = None) -> dict[str, Any]:
    d = rec.to_dict()
    d["file_url"] = _file_url(rec.local_path)
    if target_pos is not None:
        d["target_predicted_ra"], d["target_predicted_dec"] = target_pos
    return d


def _predictions(pos: dict[str, Any], recs: list[ObservationRecord]) -> list[tuple[float, float] | None]:
    tgt = pos.get("target")
    if not tgt or tgt.get("pm_ra_cosdec_mas_yr") is None:
        return [None] * len(recs)
    target = ResolvedTarget(**tgt)
    times = [r.mjd_start for r in recs]
    preds = predicted_positions(target, [t for t in times if t is not None])
    it = iter(preds)
    return [next(it) if t is not None else None for t in times]


def _split_ints(text: str | None) -> list[int] | None:
    if not text:
        return None
    try:
        return [int(x.strip().upper().lstrip("D")) for x in text.split(",") if x.strip()]
    except ValueError as exc:
        raise CoordinateError(f"Detectors must be a comma-separated list like '1,2' or 'D1,D2': {text!r}") from exc


def _range(lo: Any, hi: Any) -> tuple[Any, Any] | None:
    return None if lo in (None, "") and hi in (None, "") else (lo, hi)


# --------------------------------------------------------------------------
# request bodies
# --------------------------------------------------------------------------
class SearchRequest(BaseModel):
    q: str | None = Field(None, description="Object name or coordinate string.")
    ra: float | str | None = Field(None, description="Decimal degrees or sexagesimal hours.")
    dec: float | str | None = Field(None, description="Decimal degrees or sexagesimal degrees.")
    fov: float = Field(0.1, description="Field-of-view width in degrees.")
    date_start: str | None = Field(None, description="ISO date/time or MJD.")
    date_end: str | None = None
    release: str | list[str] | None = Field(None, description="e.g. 'DR1', 'DR1,QR3', 'all'.")
    wavelength_min: float | None = Field(None, description="Micrometres.")
    wavelength_max: float | None = None
    detectors: list[int] | None = Field(None, description="Detector arrays 1-6.")
    epoch: str | None = Field(None, description="Epoch for propagating a named target's position.")


class CutoutRequest(SearchRequest):
    observation_ids: list[str] | None = Field(
        None, description="Download only these observations (default: all search results).")
    track_target: bool = Field(
        False, description="Centre each cutout on the named target's predicted position at that date "
                           "(object stays centred) instead of one fixed centre (object moves; blink).")
    max_workers: int | None = Field(None, ge=1, le=16)
    force: bool = False


def _search(req: SearchRequest) -> tuple[dict[str, Any], list[ObservationRecord]]:
    pos = _position(req.ra, req.dec, req.q, req.epoch)
    recs = search_observations(
        pos["ra"], pos["dec"], req.fov,
        date_range=_range(req.date_start, req.date_end),
        release=req.release,
        wavelength_range=_range(req.wavelength_min, req.wavelength_max),
        detectors=req.detectors,
    )
    return pos, recs


def _search_response(pos: dict[str, Any], req: SearchRequest, recs: list[ObservationRecord]) -> dict[str, Any]:
    preds = _predictions(pos, recs)
    return {
        "query": {**pos, "fov": req.fov,
                  "releases": [r.key for r in get_settings().resolve_releases(req.release)]},
        "count": len(recs),
        "epochs": [{k: v for k, v in e.items() if k != "observation_ids"} for e in group_epochs(recs)],
        "observations": [_record_out(r, p) for r, p in zip(recs, preds)],
    }


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
@router.get("/health")
def health() -> dict[str, Any]:
    s = get_settings()
    return {"status": "ok", "cache_dir": str(s.cache_dir), "default_releases": list(s.default_releases)}


@router.get("/releases")
def releases() -> dict[str, Any]:
    s = get_settings()
    return {"default": list(s.default_releases),
            "releases": [vars(r) for r in sorted(s.releases().values(), key=lambda r: -r.priority)]}


@router.get("/targets")
def targets() -> dict[str, Any]:
    """The five mandatory challenge targets (resolved live by name)."""
    return {"targets": [{k: v for k, v in t.items() if k != "fallback"} for t in MANDATORY_TARGETS]}


@router.get("/resolve")
def resolve(q: str = Query(..., description="Object name or coordinates"),
            epoch: str | None = Query(None, description="Propagate proper motion to this date (default: now)")):
    try:
        return resolve_target(q, epoch=epoch)
    except Exception as exc:
        raise _http_error(exc)


@router.get("/observations")
def list_observations(
    q: str | None = None, ra: str | None = None, dec: str | None = None,
    fov: float = 0.1, date_start: str | None = None, date_end: str | None = None,
    release: str | None = None, wavelength_min: float | None = None,
    wavelength_max: float | None = None, detectors: str | None = Query(None, description="e.g. '1,2' or 'D6'"),
    epoch: str | None = None,
):
    """All SPHEREx images covering a position, sorted by time."""
    try:
        req = SearchRequest(q=q, ra=ra, dec=dec, fov=fov, date_start=date_start, date_end=date_end,
                            release=release, wavelength_min=wavelength_min,
                            wavelength_max=wavelength_max, detectors=_split_ints(detectors), epoch=epoch)
        pos, recs = _search(req)
        return _search_response(pos, req, recs)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc)


@router.post("/search")
def search(req: SearchRequest):
    """Same as ``GET /api/observations`` with a JSON body."""
    try:
        pos, recs = _search(req)
        return _search_response(pos, req, recs)
    except Exception as exc:
        raise _http_error(exc)


@router.get("/observations/dates")
def observation_dates(q: str | None = None, ra: str | None = None, dec: str | None = None,
                      fov: float = 0.1, release: str | None = None, epoch: str | None = None):
    try:
        pos = _position(ra, dec, q, epoch)
        return {"query": pos, **get_available_observation_dates(pos["ra"], pos["dec"], fov, release=release)}
    except Exception as exc:
        raise _http_error(exc)


@router.get("/observations/bands")
def observation_bands(q: str | None = None, ra: str | None = None, dec: str | None = None,
                      fov: float = 0.1, release: str | None = None, epoch: str | None = None):
    try:
        pos = _position(ra, dec, q, epoch)
        return {"query": pos, "bands": get_available_bands(pos["ra"], pos["dec"], fov, release=release)}
    except Exception as exc:
        raise _http_error(exc)


@router.get("/observations/{observation_id}")
def observation(observation_id: str):
    try:
        return _record_out(get_observation_metadata(observation_id))
    except Exception as exc:
        raise _http_error(exc)


@router.post("/cutouts")
def cutouts(req: CutoutRequest):
    """Download (or reuse cached) FITS cutouts for a search and return enriched records.

    Synchronous: for ~100 uncached cutouts expect 1-3 minutes. Cached
    cutouts return immediately. Use ``detectors`` / dates to limit the set.
    """
    try:
        pos, recs = _search(req)
        if req.observation_ids:
            wanted = set(req.observation_ids)
            recs = [r for r in recs if r.observation_id in wanted]
            missing = wanted - {r.observation_id for r in recs}
            if missing:
                raise KeyError(f"Not in search results for this position: {sorted(missing)[:5]}")
        preds = _predictions(pos, recs)
        centers = None
        if req.track_target:
            if all(p is None for p in preds):
                raise CoordinateError("track_target needs a named target with known proper motion (use 'q').")
            centers = {r.observation_id: p for r, p in zip(recs, preds) if p is not None}
        batch = download_cutouts(recs, pos["ra"], pos["dec"], req.fov, max_workers=req.max_workers,
                                 force=req.force, centers=centers)
        pred_by_id = {r.observation_id: p for r, p in zip(recs, preds)}
        out = batch.to_dict()
        out["observations"] = [_record_out(r, pred_by_id.get(r.observation_id)) for r in batch.records]
        out["query"] = {**pos, "fov": req.fov, "track_target": req.track_target}
        return out
    except Exception as exc:
        raise _http_error(exc)


@router.get("/files/cutouts/{file_path:path}")
def cutout_file(file_path: str):
    """Serve a cached cutout FITS (or its JSON sidecar)."""
    root = get_settings().cutout_dir.resolve()
    target = (root / file_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(404, "Not found")
    if not target.is_file() or target.suffix not in {".fits", ".json"}:
        raise HTTPException(404, "Not found")
    media = "application/fits" if target.suffix == ".fits" else "application/json"
    return FileResponse(target, media_type=media, filename=target.name)
