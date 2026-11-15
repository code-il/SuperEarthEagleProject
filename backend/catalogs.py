"""Reference catalogues for viewer overlays: Gaia DR3 and CatWISE2020.

Browsers cannot query these archives directly (CORS), so the backend proxies
small cone searches and returns sources with positions *and* proper motions
in one convention, so the viewer can draw each star where it should be on
any SPHEREx date.

* Gaia DR3: ESA Gaia archive TAP (``gaiadr3.gaia_source``), reference epoch 2016.0.
* CatWISE2020: IRSA simple cone search (``catwise_2020``; TAP as fallback), positions at each source's
  ``meanobsmjd`` (around 2015.4); proper motions converted from arcsec/yr.

Only positions are propagated (linear proper motion, no parallax). This is
fine for drawing markers on 6" pixels, not for astrometry.
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Any

import numpy as np
from astropy.time import Time

from backend.data_acquisition.cache import JsonCache, stable_hash
from backend.data_acquisition.config import get_settings
from backend.data_acquisition.coords import CoordinateError, parse_coordinates, parse_time
from backend.data_acquisition.http import get_session, request_with_retries

log = logging.getLogger(__name__)

GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
IRSA_SCS = "https://irsa.ipac.caltech.edu/SCS"
MAX_RADIUS_DEG = 0.5
CATALOGS = ("gaia", "catwise")
CATALOG_CACHE_VERSION = 3  # bump when the per-source fields change


def _tap_csv(url: str, adql: str, extra: dict[str, str] | None = None, read_timeout: float = 45) -> list[dict[str, str]]:
    data = {"LANG": "ADQL", "FORMAT": "csv", "QUERY": adql, **(extra or {})}
    # Overlays are optional: fail within a minute or two instead of the archive-download retry policy.
    resp = request_with_retries(get_session(), "POST", url, data=data, timeout=(10, read_timeout), retries=1, backoff=1.0)
    return list(csv.DictReader(io.StringIO(resp.text)))


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def query_gaia(ra: float, dec: float, radius: float, limit: int, max_mag: float | None) -> list[dict[str, Any]]:
    mag = f" AND phot_g_mean_mag <= {float(max_mag)}" if max_mag is not None else ""
    adql = (f"SELECT TOP {int(limit)} source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag "
            f"FROM gaiadr3.gaia_source WHERE 1=CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {radius:.6f})){mag} ORDER BY phot_g_mean_mag")
    rows = _tap_csv(GAIA_TAP, adql, {"REQUEST": "doQuery"})
    ref_mjd = Time(2016.0, format="jyear").mjd
    return [{"id": f"Gaia DR3 {r['source_id']}", "ra": _f(r["ra"]), "dec": _f(r["dec"]),
             "ref_mjd": ref_mjd, "pm_ra_cosdec_mas_yr": _f(r["pmra"]), "pm_dec_mas_yr": _f(r["pmdec"]),
             "parallax_mas": _f(r["parallax"]), "mag": _f(r["phot_g_mean_mag"]), "mag_band": "G"}
            for r in rows]


def _catwise_rows(ra: float, dec: float, radius: float) -> list[dict[str, str]]:
    """CatWISE2020 rows in a cone.

    IRSA's simple cone search answers in seconds; the equivalent TAP query can
    take over a minute when the service is busy, so TAP is only the fallback.
    """
    try:
        resp = request_with_retries(get_session(), "GET", IRSA_SCS, timeout=(10, 45), retries=1, backoff=1.0,
                                    params={"table": "catwise_2020", "RA": f"{ra:.7f}", "DEC": f"{dec:.7f}",
                                            "SR": f"{radius:.6f}", "format": "csv"})
        text = resp.text
        if not text.lstrip().startswith("source_name"):
            raise ValueError(f"unexpected cone-search reply: {text[:120]!r}")
        return list(csv.DictReader(io.StringIO(text)))
    except Exception as exc:  # noqa: BLE001 - fall back to TAP on any SCS problem
        log.warning("CatWISE cone search failed (%s); trying TAP", exc)
        adql = (f"SELECT source_name, ra, dec, pmra, pmdec, w1mpro, w2mpro, meanobsmjd FROM catwise_2020 "
                f"WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {radius:.6f}))=1")
        return _tap_csv(IRSA_TAP, adql, read_timeout=150)


# Spurious CatWISE detections in the halo of a saturated star often carry large,
# formally significant but meaningless proper motions (seen around Barnard's Star).
SUSPECT_PM_MAS_YR = 1000.0
BRIGHT_W2 = 10.0
BRIGHT_HALO_ARCSEC = 150.0


def _sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    d1, d2 = np.radians(dec1), np.radians(dec2)
    c = np.sin(d1) * np.sin(d2) + np.cos(d1) * np.cos(d2) * np.cos(np.radians(ra1 - ra2))
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))) * 3600)


def query_catwise(ra: float, dec: float, radius: float, limit: int, max_mag: float | None) -> list[dict[str, Any]]:
    rows = _catwise_rows(ra, dec, radius)
    bright = [(_f(r["ra"]), _f(r["dec"])) for r in rows
              if (_f(r.get("w2mpro")) or 99) < BRIGHT_W2 and _f(r["ra"]) is not None]
    out = []
    for r in rows:
        w2 = _f(r.get("w2mpro"))
        if max_mag is not None and (w2 is None or w2 > max_mag):
            continue
        pmra, pmdec = _f(r.get("pmra")), _f(r.get("pmdec"))
        sra, sdec = _f(r["ra"]), _f(r["dec"])
        pm_tot = None if pmra is None or pmdec is None else float(np.hypot(pmra, pmdec)) * 1000.0
        cc = (r.get("cc_flags") or "").strip().strip("0")
        suspect = bool(pm_tot is not None and pm_tot > SUSPECT_PM_MAS_YR and sra is not None and (
            cc or any(_sep_arcsec(sra, sdec, b[0], b[1]) < BRIGHT_HALO_ARCSEC for b in bright)))
        out.append({"id": f"CWISE {r['source_name']}", "ra": sra, "dec": sdec,
                    "ref_mjd": _f(r.get("meanobsmjd")),
                    "pm_ra_cosdec_mas_yr": None if pmra is None else pmra * 1000.0,
                    "pm_dec_mas_yr": None if pmdec is None else pmdec * 1000.0,
                    "parallax_mas": None, "mag": w2, "mag_band": "W2", "w1_mag": _f(r.get("w1mpro")),
                    "cc_flags": r.get("cc_flags") or None,
                    # Large motion next to a saturated star or with artefact flags: probably not real.
                    "pm_suspect": suspect})
    out.sort(key=lambda x: (x["mag"] is None, x["mag"] if x["mag"] is not None else 0.0))
    return out[:limit]


def propagate(src: dict[str, Any], mjd: float) -> tuple[float, float]:
    """Linear proper-motion position at ``mjd`` (suspect motions are not applied)."""
    pa, pd = src.get("pm_ra_cosdec_mas_yr"), src.get("pm_dec_mas_yr")
    if pa is None or pd is None or src.get("ref_mjd") is None or src.get("pm_suspect"):
        return src["ra"], src["dec"]
    dt = (mjd - src["ref_mjd"]) / 365.25
    dec = src["dec"] + pd * dt / 3.6e6
    ra = src["ra"] + pa * dt / 3.6e6 / max(np.cos(np.radians(src["dec"])), 1e-6)
    return float(ra % 360.0), float(dec)


def cone_search(catalog: str, ra: Any, dec: Any, radius: float, *, epoch: Any = None,
                limit: int = 500, max_mag: float | None = None, use_cache: bool = True) -> dict[str, Any]:
    """Sources within ``radius`` deg; positions also given at ``epoch`` if supplied."""
    if catalog not in CATALOGS:
        raise CoordinateError(f"catalog must be one of {CATALOGS}")
    c = parse_coordinates(ra, dec)
    radius = float(radius)
    if not 0 < radius <= MAX_RADIUS_DEG:
        raise CoordinateError(f"radius must be in (0, {MAX_RADIUS_DEG}] deg")
    limit = int(max(1, min(limit, 5000)))
    s = get_settings()
    cache = JsonCache(s.cache_dir / "catalogs", 7 * 86400)
    # Search a slightly larger cone so fast movers near the edge are not lost.
    key = stable_hash({"v": CATALOG_CACHE_VERSION, "c": catalog, "ra": round(c.ra.deg, 5), "dec": round(c.dec.deg, 5),
                       "r": round(radius, 5), "l": limit, "m": max_mag})
    sources = cache.get(key) if use_cache else None
    if sources is None:
        fn = query_gaia if catalog == "gaia" else query_catwise
        sources = [x for x in fn(c.ra.deg, c.dec.deg, radius, limit, max_mag)
                   if x["ra"] is not None and x["dec"] is not None]
        cache.set(key, sources)
    out = {"catalog": catalog, "center_ra": c.ra.deg, "center_dec": c.dec.deg, "radius_deg": radius,
           "count": len(sources), "sources": sources,
           "note": "Positions at ref_mjd; propagate with pm_ra_cosdec_mas_yr / pm_dec_mas_yr (linear)."}
    if epoch is not None:
        mjd = parse_time(epoch).mjd
        out["epoch_mjd"] = mjd
        for src in sources:
            src["ra_at_epoch"], src["dec_at_epoch"] = propagate(src, mjd)
    return out
