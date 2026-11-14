"""Querying the IRSA archive for SPHEREx spectral images (SIA2 via PyVO).

Each SIA2 row is one Level-2 spectral image: one ~113 s exposure on one of
the six detector arrays. A sky position is re-observed at many dates, and
within one visit by all six detectors (each covering a different wavelength
range through its linear variable filter). So a "time series" for a position
is the set of rows sorted by time, usually grouped per detector.

SPHEREx revisits are not regular: do not assume six-month spacing. Use
:func:`get_available_observation_dates` to see the actual epochs.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.time import Time

from .cache import JsonCache, atomic_write_json, stable_hash
from .config import Release, get_settings
from .coords import (CoordinateError, parse_coordinates, parse_date_range,
                     parse_wavelength_range, validate_fov)
from .models import ObservationRecord

log = logging.getLogger(__name__)

# SIA2/ObsCore columns we keep in the query cache.
_COLUMNS = [
    "obs_id", "obs_publisher_did", "obs_collection", "s_ra", "s_dec", "s_fov", "s_region",
    "s_resolution", "s_pixel_scale", "s_xel1", "s_xel2", "t_min", "t_max", "t_exptime",
    "em_min", "em_max", "em_res_power", "energy_bandpassname", "calib_level",
    "instrument_name", "facility_name", "access_url", "access_format", "access_estsize",
    "target_name", "obs_release_date", "dataproduct_type", "dataproduct_subtype", "cloud_access",
]


class ArchiveError(RuntimeError):
    """The archive could not be queried."""


# --------------------------------------------------------------------------
# Low-level query (cached)
# --------------------------------------------------------------------------
def _to_py(v: Any) -> Any:
    if v is None or v is np.ma.masked:
        return None
    if isinstance(v, np.ma.core.MaskedConstant):
        return None
    if isinstance(v, bytes):
        v = v.decode()
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and not np.isfinite(v):
        return None
    if isinstance(v, str) and v == "":
        return None
    return v


def _sia_query(collection: str, coord: SkyCoord, radius_deg: float,
               time_range: tuple[Time, Time] | None,
               wl_range_um: tuple[float, float] | None) -> list[dict[str, Any]]:
    import pyvo

    s = get_settings()
    params: dict[str, Any] = {"pos": (coord, radius_deg * u.deg), "collection": collection,
                              "maxrec": 200000}
    if time_range is not None:
        params["time"] = time_range
    if wl_range_um is not None:
        params["band"] = (wl_range_um[0] * u.um, wl_range_um[1] * u.um)
    try:
        svc = pyvo.dal.SIA2Service(s.sia_url, check_baseurl=False)
        table = svc.search(**params).to_table()
    except Exception as exc:
        raise ArchiveError(f"IRSA SIA2 query failed for {collection}: {exc}") from exc
    cols = [c for c in _COLUMNS if c in table.colnames]
    return [{c: _to_py(row[c]) for c in cols} for row in table]


def _cached_query(release: Release, coord: SkyCoord, radius_deg: float,
                  time_range: tuple[Time, Time] | None,
                  wl_range_um: tuple[float, float] | None,
                  use_cache: bool) -> list[dict[str, Any]]:
    s = get_settings()
    cache = JsonCache(s.query_cache_dir, s.query_cache_ttl_s)
    key = stable_hash({
        "c": release.collection, "ra": round(coord.ra.deg, 6), "dec": round(coord.dec.deg, 6),
        "r": round(radius_deg, 6),
        "t": None if time_range is None else [time_range[0].mjd, time_range[1].mjd],
        "w": wl_range_um, "v": 1,
    })
    if use_cache:
        hit = cache.get(key)
        if hit is not None:
            log.debug("query cache hit %s %s", release.collection, key)
            return hit
    rows = _sia_query(release.collection, coord, radius_deg, time_range, wl_range_um)
    cache.set(key, rows)
    return rows


# --------------------------------------------------------------------------
# Row -> record
# --------------------------------------------------------------------------
_DET_RE = re.compile(r"D(\d)\s*$")


def _detector_from(row: dict[str, Any]) -> int | None:
    for field in ("energy_bandpassname", "obs_publisher_did"):
        val = row.get(field)
        if val:
            m = _DET_RE.search(str(val))
            if m:
                return int(m.group(1))
    return None


def _iso(mjd: float | None) -> str | None:
    if mjd is None:
        return None
    return Time(mjd, format="mjd", scale="utc").isot + "Z"


def _um(meters: float | None) -> float | None:
    return None if meters is None else round(meters * 1e6, 5)


def row_to_record(row: dict[str, Any], release: Release) -> ObservationRecord:
    det = _detector_from(row)
    obs_id = row.get("obs_id")
    oid = f"{obs_id}_D{det}" if det is not None else str(row.get("obs_publisher_did") or obs_id)
    cloud = row.get("cloud_access")
    if isinstance(cloud, str):
        try:
            cloud = json.loads(cloud)
        except json.JSONDecodeError:
            cloud = None
    xel = [row.get("s_xel1"), row.get("s_xel2")]
    est = row.get("access_estsize")
    return ObservationRecord(
        observation_id=oid,
        observation_time=_iso(row.get("t_min")),
        ra=row.get("s_ra"),
        dec=row.get("s_dec"),
        wavelength_min=_um(row.get("em_min")),
        wavelength_max=_um(row.get("em_max")),
        band=row.get("energy_bandpassname"),
        detector=det,
        data_release=release.key,
        calibration_level=row.get("calib_level"),
        quality_flags=[],
        fits_url=row.get("access_url"),
        local_path=None,
        obs_id=obs_id,
        publisher_did=row.get("obs_publisher_did"),
        collection=row.get("obs_collection") or release.collection,
        observation_time_end=_iso(row.get("t_max")),
        mjd_start=row.get("t_min"),
        mjd_end=row.get("t_max"),
        exposure_time_s=row.get("t_exptime"),
        instrument=row.get("instrument_name"),
        pixel_scale_arcsec=row.get("s_pixel_scale"),
        image_size_px=[int(x) for x in xel] if all(x is not None for x in xel) else None,
        image_fov_deg=row.get("s_fov"),
        spectral_resolving_power=row.get("em_res_power"),
        s_region=row.get("s_region"),
        archive_release_date=row.get("obs_release_date"),
        estimated_size_kb=int(est) if est is not None else None,
        survey_field=row.get("target_name"),
        cloud_access=cloud if isinstance(cloud, dict) else None,
    )


# --------------------------------------------------------------------------
# Record index (lets get_observation_metadata work without a new query)
# --------------------------------------------------------------------------
_index: dict[str, ObservationRecord] = {}


def _safe_id(observation_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", observation_id)


def remember(records: Iterable[ObservationRecord]) -> None:
    d = get_settings().record_cache_dir
    for r in records:
        prev = _index.get(r.observation_id)
        if prev is not None:
            # keep enrichment gathered from downloads
            for f in ("quality", "quality_flags", "wavelength_at_target", "bandwidth_at_target",
                      "local_path", "cutout"):
                if getattr(r, f) in (None, []) and getattr(prev, f) not in (None, []):
                    setattr(r, f, getattr(prev, f))
        _index[r.observation_id] = r
        atomic_write_json(d / f"{_safe_id(r.observation_id)}.json", r.to_dict())


def _load_record(observation_id: str) -> ObservationRecord | None:
    if observation_id in _index:
        return _index[observation_id]
    path = get_settings().record_cache_dir / f"{_safe_id(observation_id)}.json"
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            rec = ObservationRecord.from_dict(json.load(fh))
        _index[observation_id] = rec
        return rec
    return None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def search_observations(
    ra: Any,
    dec: Any,
    fov: Any = 0.1,
    date_range: Any = None,
    release: str | list[str] | None = None,
    wavelength_range: Any = None,
    detectors: Iterable[int] | None = None,
    use_cache: bool = True,
) -> list[ObservationRecord]:
    """Find every SPHEREx spectral image overlapping a sky region.

    Parameters
    ----------
    ra, dec : float or str
        Decimal degrees, or sexagesimal strings (RA in hours). See
        :func:`coords.parse_coordinates`.
    fov : float
        Field-of-view width in degrees. Images overlapping a circle of
        diameter ``fov`` around the position are returned.
    date_range : (start, end) or None
        ISO dates/times or MJD; either side may be None.
    release : str | list[str] | None
        ``"DR1"``, ``"QR3"``, ``"DR1,QR3"``, ``"all"``... Default:
        ``settings.default_releases``.
    wavelength_range : (min_um, max_um) or None
        Keep images whose wavelength coverage overlaps this range.
    detectors : iterable of int, optional
        Keep only these detector arrays (1-6).

    Returns
    -------
    list[ObservationRecord] sorted chronologically, then by detector. If the
    same exposure appears in several releases, only the highest-priority
    release's record is kept.
    """
    coord = parse_coordinates(ra, dec)
    fov = validate_fov(fov)
    time_range = parse_date_range(date_range)
    wl_range = parse_wavelength_range(wavelength_range)
    releases = get_settings().resolve_releases(release)
    det_filter = None
    if detectors is not None:
        det_filter = {int(d) for d in detectors}
        if not det_filter <= {1, 2, 3, 4, 5, 6}:
            raise CoordinateError("Detectors must be in 1..6.")
    radius = fov / 2.0

    def run(rel: Release) -> tuple[Release, list[dict[str, Any]]]:
        return rel, _cached_query(rel, coord, radius, time_range, wl_range, use_cache)

    with ThreadPoolExecutor(max_workers=max(1, len(releases))) as pool:
        results = list(pool.map(run, releases))

    best: dict[str, tuple[int, ObservationRecord]] = {}
    for rel, rows in results:
        for row in rows:
            if row.get("dataproduct_subtype") not in (None, "science"):
                continue
            rec = row_to_record(row, rel)
            if det_filter is not None and rec.detector not in det_filter:
                continue
            rec.covers_position = region_contains(rec.s_region, coord.ra.deg, coord.dec.deg)
            prev = best.get(rec.observation_id)
            if prev is None or rel.priority > prev[0]:
                best[rec.observation_id] = (rel.priority, rec)
    records = sorted((r for _, r in best.values()),
                     key=lambda r: (r.mjd_start if r.mjd_start is not None else np.inf,
                                    r.detector or 0, r.observation_id))
    remember(records)
    log.info("search %.5f %+.5f fov=%.4f releases=%s -> %d images",
             coord.ra.deg, coord.dec.deg, fov, [r.key for r in releases], len(records))
    return records


def get_observation_metadata(observation_id: str) -> ObservationRecord:
    """Metadata for one observation ID (``<obs_id>_D<n>``).

    Uses the local record index first (filled by every search and download);
    otherwise looks the exposure up in IRSA's ObsCore table via TAP.
    Raises ``KeyError`` if the ID does not exist.
    """
    rec = _load_record(observation_id)
    if rec is not None:
        return rec
    m = re.match(r"^(?P<obs>.+)_D(?P<det>\d)$", observation_id or "")
    if not m:
        raise KeyError(f"Malformed observation ID {observation_id!r}; expected '<obs_id>_D<1-6>'.")
    obs, det = m.group("obs"), int(m.group("det"))
    if not re.match(r"^[A-Za-z0-9_]+$", obs):
        raise KeyError(f"Malformed observation ID {observation_id!r}.")
    s = get_settings()
    releases = sorted(s.releases().values(), key=lambda r: -r.priority)
    import pyvo

    cols = ", ".join(_COLUMNS)
    collections = ", ".join(f"'{r.collection}'" for r in releases)
    adql = (f"SELECT {cols} FROM ivoa.obscore WHERE obs_id = '{obs}' "
            f"AND energy_bandpassname = 'SPHEREx-D{det}' AND obs_collection IN ({collections})")
    try:
        table = pyvo.dal.TAPService(s.tap_url).run_sync(adql).to_table()
    except Exception as exc:
        raise ArchiveError(f"IRSA TAP lookup failed for {observation_id}: {exc}") from exc
    by_coll = {r.collection: r for r in releases}
    found: list[tuple[Release, dict[str, Any]]] = []
    for row in table:
        d = {c: _to_py(row[c]) for c in table.colnames}
        rel = by_coll.get(d.get("obs_collection"))
        if rel:
            found.append((rel, d))
    if not found:
        raise KeyError(f"Observation {observation_id!r} not found in {[r.key for r in releases]}.")
    rel, row = max(found, key=lambda x: x[0].priority)
    # ivoa.obscore's access_url is a DataLink URL, not the image itself, so
    # re-fetch the exact SIA2 row (tiny cone at the image centre + time window)
    # to return a record identical to what search_observations produces.
    rec = None
    if row.get("s_ra") is not None and row.get("s_dec") is not None and row.get("t_min") is not None:
        t = row["t_min"]
        rows = _sia_query(rel.collection, SkyCoord(row["s_ra"], row["s_dec"], unit="deg"), 0.001,
                          (Time(t - 0.01, format="mjd"), Time(t + 0.01, format="mjd")), None)
        for r in rows:
            cand = row_to_record(r, rel)
            if cand.observation_id == observation_id:
                rec = cand
                break
    if rec is None:  # fall back to the TAP row, without the DataLink URL
        rec = row_to_record(row, rel)
        if rec.fits_url and "datalink" in rec.fits_url:
            rec.fits_url = None
    remember([rec])
    return rec


def region_contains(s_region: str | None, ra: float, dec: float) -> bool | None:
    """Whether an ``s_region`` ``POLYGON`` contains (ra, dec). ``None`` if unknown.

    Vertices are projected onto the tangent plane at (ra, dec) (gnomonic), then
    a standard ray-casting test is applied; fine for SPHEREx's 3.5 deg images.
    """
    if not s_region:
        return None
    parts = s_region.split()
    if len(parts) < 8 or parts[0].upper() != "POLYGON":
        return None
    try:
        nums = [float(x) for x in parts[2:] if not x.isalpha()]
    except ValueError:
        return None
    v = np.array(nums).reshape(-1, 2)
    ra0, dec0 = np.radians(ra), np.radians(dec)
    a, d = np.radians(v[:, 0]), np.radians(v[:, 1])
    cosc = np.sin(dec0) * np.sin(d) + np.cos(dec0) * np.cos(d) * np.cos(a - ra0)
    if np.any(cosc <= 0):
        return False  # vertex on the far hemisphere: certainly not containing
    x = np.cos(d) * np.sin(a - ra0) / cosc
    y = (np.cos(dec0) * np.sin(d) - np.sin(dec0) * np.cos(d) * np.cos(a - ra0)) / cosc
    inside = False
    n = len(x)
    for i in range(n):
        j = (i - 1) % n
        if (y[i] > 0) != (y[j] > 0):
            xc = x[i] + (0 - y[i]) * (x[j] - x[i]) / (y[j] - y[i])
            if xc > 0:
                inside = not inside
    return inside


def group_epochs(records: list[ObservationRecord], gap_days: float | None = None) -> list[dict[str, Any]]:
    """Group chronologically sorted records into survey epochs (a gap > ``gap_days`` starts a new one)."""
    gap = get_settings().epoch_gap_days if gap_days is None else gap_days
    recs = sorted((r for r in records if r.mjd_start is not None), key=lambda r: r.mjd_start)
    epochs: list[list[ObservationRecord]] = []
    for r in recs:
        if not epochs or r.mjd_start - epochs[-1][-1].mjd_start > gap:
            epochs.append([r])
        else:
            epochs[-1].append(r)
    out = []
    for i, ep in enumerate(epochs):
        per_det: dict[str, int] = {}
        for r in ep:
            k = f"D{r.detector}" if r.detector else "unknown"
            per_det[k] = per_det.get(k, 0) + 1
        mjds = [r.mjd_start for r in ep]
        out.append({
            "epoch_index": i,
            "start": _iso(min(mjds)), "end": _iso(max(mjds)),
            "mjd_start": min(mjds), "mjd_end": max(mjds), "mjd_mean": float(np.mean(mjds)),
            "n_observations": len(ep),
            "n_per_detector": dict(sorted(per_det.items())),
            "releases": sorted({r.data_release for r in ep if r.data_release}),
            "observation_ids": [r.observation_id for r in ep],
        })
    return out


def get_available_observation_dates(ra: Any, dec: Any, fov: Any = 0.1, **search_kwargs: Any) -> dict[str, Any]:
    """Distinct observation dates and survey epochs covering a position."""
    recs = search_observations(ra, dec, fov, **search_kwargs)
    by_day: dict[str, dict[str, Any]] = {}
    for r in recs:
        if r.observation_time is None:
            continue
        day = r.observation_time[:10]
        d = by_day.setdefault(day, {"date": day, "n_observations": 0, "detectors": set(), "releases": set()})
        d["n_observations"] += 1
        if r.detector:
            d["detectors"].add(r.detector)
        if r.data_release:
            d["releases"].add(r.data_release)
    dates = [{**d, "detectors": sorted(d["detectors"]), "releases": sorted(d["releases"])}
             for _, d in sorted(by_day.items())]
    return {
        "n_observations": len(recs),
        "first": recs[0].observation_time if recs else None,
        "last": recs[-1].observation_time if recs else None,
        "dates": dates,
        "epochs": group_epochs(recs),
    }


DETECTOR_INFO = {
    1: "SWIR array (short-wave infrared)", 2: "SWIR array (short-wave infrared)",
    3: "SWIR array (short-wave infrared)", 4: "MWIR array (mid-wave infrared)",
    5: "MWIR array (mid-wave infrared)", 6: "MWIR array (mid-wave infrared)",
}


def get_available_bands(ra: Any, dec: Any, fov: Any = 0.1, **search_kwargs: Any) -> list[dict[str, Any]]:
    """Detector bands available at a position, with their wavelength coverage.

    Wavelength ranges are the archive's ``em_min``/``em_max`` for each image
    (the full range of that detector's linear variable filter). The exact
    wavelength *at the target* depends on where the target falls on the
    detector in each exposure; it is filled in per observation
    (``wavelength_at_target``) once a cutout has been downloaded.
    """
    recs = search_observations(ra, dec, fov, **search_kwargs)
    bands: dict[int | None, dict[str, Any]] = {}
    for r in recs:
        b = bands.setdefault(r.detector, {
            "band": r.band, "detector": r.detector, "description": DETECTOR_INFO.get(r.detector),
            "wavelength_min": r.wavelength_min, "wavelength_max": r.wavelength_max,
            "n_observations": 0, "first": r.observation_time, "last": r.observation_time,
            "releases": set(), "spectral_resolving_power": r.spectral_resolving_power,
        })
        b["n_observations"] += 1
        if r.wavelength_min is not None:
            b["wavelength_min"] = min(x for x in (b["wavelength_min"], r.wavelength_min) if x is not None)
        if r.wavelength_max is not None:
            b["wavelength_max"] = max(x for x in (b["wavelength_max"], r.wavelength_max) if x is not None)
        if r.observation_time:
            b["first"] = min(b["first"] or r.observation_time, r.observation_time)
            b["last"] = max(b["last"] or r.observation_time, r.observation_time)
        if r.data_release:
            b["releases"].add(r.data_release)
    out = []
    for det in sorted(bands, key=lambda d: (d is None, d)):
        b = bands[det]
        b["releases"] = sorted(b["releases"])
        out.append(b)
    return out
