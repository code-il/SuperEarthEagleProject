"""Downloading SPHEREx cutouts and full images, with caching and retries.

Cutouts come from IRSA's on-the-fly cutout service, which is driven by query
parameters on the image URL::

    <access_url>?center=<ra>,<dec>d&size=<deg>

The service returns a complete multi-extension FITS (IMAGE, FLAGS, VARIANCE,
ZODI, EPSF, WCS-WAVE) with every header preserved, so no metadata is lost.
Files are written atomically, verified as FITS before being kept, and
re-used on later requests (same image + same centre + same size).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from .archive import remember
from .cache import _replace_with_retry, atomic_write_json, key_lock
from .config import get_settings
from .coords import parse_coordinates, validate_fov
from .fits_meta import extract_fits_metadata, is_valid_spherex_fits
from .http import HttpError, get_session, request_with_retries
from .models import ObservationRecord

log = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]
"""Called with progress events: ``{"event": ..., "done": n, "total": m, ...}``."""


class DownloadError(RuntimeError):
    pass


def _check_url(access_url: str) -> str:
    parts = urlsplit(access_url)
    if parts.scheme != "https" or not parts.netloc.endswith("ipac.caltech.edu"):
        raise DownloadError(f"Refusing to download from non-IRSA URL: {access_url!r}")
    if parts.query:
        raise DownloadError("access_url must be the plain image URL (no query string).")
    return access_url


def build_cutout_url(access_url: str, ra: float, dec: float, size_deg: float) -> str:
    return f"{_check_url(access_url)}?center={ra:.7f},{dec:.7f}d&size={size_deg:.6f}"


def _stem(access_url: str) -> str:
    name = Path(urlsplit(access_url).path).name
    return re.sub(r"\.fits(\.gz)?$", "", name) or hashlib.sha1(access_url.encode()).hexdigest()[:16]


def cutout_path(access_url: str, ra: float, dec: float, size_deg: float,
                output_directory: str | Path | None = None) -> Path:
    base = Path(output_directory) if output_directory else get_settings().cutout_dir
    return base / _stem(access_url) / f"cut_{ra:.5f}_{dec:+.5f}_{size_deg:.4f}.fits"


def _fetch_to_file(url: str, dest: Path, progress: ProgressCallback | None = None,
                   chunk: int = 1 << 16) -> tuple[int, str]:
    """Stream ``url`` to ``dest`` atomically; returns (bytes, sha256)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f".{os.getpid()}.{threading.get_ident()}.part")
    sha = hashlib.sha256()
    n = 0
    resp = request_with_retries(get_session(), "GET", url, stream=True)
    total = int(resp.headers.get("Content-Length") or 0) or None
    try:
        with open(tmp, "wb") as fh:
            for block in resp.iter_content(chunk):
                if not block:
                    continue
                fh.write(block)
                sha.update(block)
                n += len(block)
                if progress and total:
                    progress({"event": "bytes", "url": url, "bytes": n, "total_bytes": total})
        if total is not None and n != total:
            raise DownloadError(f"Truncated download from {url}: {n} of {total} bytes.")
        head = tmp.read_bytes()[:30] if n else b""
        if not head.startswith(b"SIMPLE"):
            text = head.decode(errors="replace")
            raise DownloadError(f"Server did not return a FITS file for {url}: {text!r}")
        if not is_valid_spherex_fits(tmp):
            raise DownloadError(f"Downloaded file from {url} is not a valid SPHEREx image.")
        _replace_with_retry(tmp, dest)
    finally:
        resp.close()
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return n, sha.hexdigest()


def _sidecar(path: Path) -> Path:
    return path.with_suffix(".json")


def download_cutout(
    access_url: str,
    ra: Any,
    dec: Any,
    fov: Any,
    output_directory: str | Path | None = None,
    record: ObservationRecord | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Download (or reuse) a square cutout of ``fov`` degrees centred on ``ra, dec``.

    Returns a dict with ``local_path``, ``url``, ``bytes``, ``sha256``,
    ``from_cache`` and ``metadata`` (observation time, detector, wavelength at
    the target, quality flags... read from the FITS itself). When ``record``
    is given it is enriched in place (``local_path``, ``quality_flags``,
    ``wavelength_at_target``...).
    """
    c = parse_coordinates(ra, dec)
    size = validate_fov(fov)
    ra_d, dec_d = float(c.ra.deg), float(c.dec.deg)
    url = build_cutout_url(access_url, ra_d, dec_d, size)
    path = cutout_path(access_url, ra_d, dec_d, size, output_directory)
    side = _sidecar(path)

    with key_lock(str(path)):
        from_cache = (not force) and path.exists() and side.exists() and is_valid_spherex_fits(path)
        if from_cache:
            import json

            with open(side, encoding="utf-8") as fh:
                info = json.load(fh)
        else:
            try:
                nbytes, sha = _fetch_to_file(url, path, progress)
            except HttpError as exc:
                if exc.status == 422:
                    raise DownloadError(
                        f"IRSA refused the cutout (HTTP 422): the centre {ra_d:.5f}, {dec_d:+.5f} is "
                        f"probably outside this image's footprint.") from exc
                raise
            meta = extract_fits_metadata(path, ra_d, dec_d)
            info = {
                "local_path": str(path),
                "url": url,
                "source_image_url": access_url,
                "center_ra": ra_d, "center_dec": dec_d, "size_deg": size,
                "bytes": nbytes, "sha256": sha,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "metadata": meta,
            }
            atomic_write_json(side, info)
    info = {**info, "local_path": str(path), "from_cache": from_cache}
    if record is not None:
        enrich_record(record, info)
    return info


def enrich_record(record: ObservationRecord, info: dict[str, Any]) -> ObservationRecord:
    """Copy file-derived values (path, quality, wavelength) into a record."""
    meta = info.get("metadata") or {}
    record.local_path = info.get("local_path")
    record.quality_flags = list(meta.get("quality_flags") or [])
    record.wavelength_at_target = meta.get("wavelength_um")
    record.bandwidth_at_target = meta.get("bandwidth_um")
    flags = meta.get("flags") or {}
    record.quality = {
        "l1_dqa": meta.get("l1_dqa"), "l2_dqa": meta.get("l2_dqa"),
        "psf_fwhm": meta.get("psf_fwhm"),
        "target_in_image": meta.get("target_in_image"),
        "target_pixel_flags": flags.get("target_pixel_flags"),
        "target_box_flags": flags.get("target_box_flags"),
        "fraction_with_problem_flags": flags.get("fraction_with_problem_flags"),
        "fraction_by_flag": flags.get("fraction_by_flag"),
        "pipeline_version": meta.get("pipeline_version"),
    }
    record.cutout = {k: info.get(k) for k in
                     ("url", "center_ra", "center_dec", "size_deg", "bytes", "sha256", "retrieved_at")}
    # Values from the file header are authoritative; keep archive values where missing
    if meta.get("exposure_time_s") is not None and record.exposure_time_s is None:
        record.exposure_time_s = meta["exposure_time_s"]
    remember([record])
    return record


def download_observation(
    access_url: str,
    output_directory: str | Path | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Download a complete Level-2 image (2040x2040, ~60-70 MB) unchanged.

    Prefer :func:`download_cutout`; use this only when the whole detector
    frame is genuinely needed.
    """
    _check_url(access_url)
    base = Path(output_directory) if output_directory else get_settings().full_image_dir
    path = base / Path(urlsplit(access_url).path).name
    with key_lock(str(path)):
        if not force and path.exists() and is_valid_spherex_fits(path):
            return {"local_path": str(path), "url": access_url, "from_cache": True,
                    "bytes": path.stat().st_size}
        nbytes, sha = _fetch_to_file(access_url, path, progress)
    return {"local_path": str(path), "url": access_url, "from_cache": False,
            "bytes": nbytes, "sha256": sha}


@dataclass
class BatchResult:
    records: list[ObservationRecord] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    n_from_cache: int = 0
    n_downloaded: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_requested": len(self.records) + len(self.errors) + len(self.skipped),
            "n_ok": len(self.records), "n_failed": len(self.errors), "n_skipped": len(self.skipped),
            "n_from_cache": self.n_from_cache, "n_downloaded": self.n_downloaded,
            "observations": [r.to_dict() for r in self.records],
            "errors": self.errors,
            "skipped": self.skipped,
        }


def download_cutouts(
    records: Iterable[ObservationRecord],
    ra: Any,
    dec: Any,
    fov: Any,
    output_directory: str | Path | None = None,
    max_workers: int | None = None,
    progress: ProgressCallback | None = None,
    force: bool = False,
    centers: dict[str, tuple[float, float]] | None = None,
    skip_non_covering: bool = True,
) -> BatchResult:
    """Download cutouts for many observations in parallel.

    By default every cutout is centred on the same ``ra, dec`` (what a blink
    comparison needs: moving objects visibly shift). ``centers`` can map
    ``observation_id -> (ra, dec)`` to centre individual cutouts elsewhere,
    e.g. on a target's proper-motion-predicted position at each date.

    Images whose footprint does not contain the position
    (``covers_position is False``: the search circle only clips their edge)
    are skipped by default, because IRSA refuses such cutouts; they are
    listed under ``skipped``.

    Failures are collected (not raised) so one bad image never sinks a time
    series. Results stay in chronological order.
    """
    centers = centers or {}
    result = BatchResult()
    recs = []
    for r in records:
        if not r.fits_url:
            result.skipped.append({"observation_id": r.observation_id, "reason": "no image URL"})
        elif skip_non_covering and r.covers_position is False and r.observation_id not in centers:
            result.skipped.append({"observation_id": r.observation_id,
                                   "reason": "position is outside this image's footprint"})
        else:
            recs.append(r)
    s = get_settings()
    if len(recs) > s.max_cutouts_per_request:
        raise DownloadError(f"Too many cutouts requested ({len(recs)}); "
                            f"limit is {s.max_cutouts_per_request}. Narrow the date range or detectors.")
    workers = max_workers or s.max_workers
    total = len(recs)
    ok: dict[str, ObservationRecord] = {}
    done = 0
    lock = threading.Lock()

    def job(r: ObservationRecord) -> tuple[ObservationRecord, dict[str, Any]]:
        cra, cdec = centers.get(r.observation_id, (ra, dec))
        return r, download_cutout(r.fits_url, cra, cdec, fov, output_directory, record=r, force=force)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(job, r): r for r in recs}
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                _, info = fut.result()
                ok[r.observation_id] = r
                status = "cached" if info["from_cache"] else "downloaded"
                with lock:
                    if info["from_cache"]:
                        result.n_from_cache += 1
                    else:
                        result.n_downloaded += 1
            except (HttpError, DownloadError, OSError, ValueError) as exc:
                status = "failed"
                result.errors.append({"observation_id": r.observation_id, "url": r.fits_url,
                                      "error": str(exc)})
                log.warning("cutout failed for %s: %s", r.observation_id, exc)
            with lock:
                done += 1
                if progress:
                    progress({"event": "cutout", "observation_id": r.observation_id,
                              "status": status, "done": done, "total": total})
    result.records = [r for r in recs if r.observation_id in ok]
    return result
