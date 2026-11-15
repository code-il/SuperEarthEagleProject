"""Catalogue cross-matching (section F).

* **Gaia DR3** and **CatWISE2020** through ``backend.catalogs.cone_search``
  (shared with the viewer overlays and cached for 7 days).
* **SIMBAD** through its TAP service (CDS, with the Harvard mirror as a
  fallback) for names and object types.
* **SkyBoT** (IMCCE) for known solar-system objects at a given time.

Every query reports a status (``ok`` / ``unavailable`` / ``error``). The
classifier never calls a source "new" because a catalogue had no match:
an unmatched source stays a *candidate*, and an unreachable catalogue is
listed as unchecked.
"""
from __future__ import annotations

import csv
import io
import logging
import math
from typing import Any, Iterable

import numpy as np

from backend import catalogs as cat
from backend.data_acquisition.cache import JsonCache, stable_hash
from backend.data_acquisition.config import get_settings
from backend.data_acquisition.coords import parse_time
from backend.data_acquisition.http import get_session, request_with_retries

from .motion import DAYS_PER_YEAR, predict_position, separation_arcsec

log = logging.getLogger(__name__)

SIMBAD_TAP = ("https://simbad.cds.unistra.fr/simbad/sim-tap/sync",
              "https://simbad.harvard.edu/simbad/sim-tap/sync")
SKYBOT_URL = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
J2000_MJD = 51544.5
MATCH_RADIUS_ARCSEC = 4.0
"""Base match radius (2/3 of a 6.15" pixel) added in quadrature to 3 sigma of the position."""
DEFAULT_CATALOGS = ("gaia", "catwise", "simbad")
MAX_POS_ERR_ARCSEC = 2.0
MAX_GATE_ARCSEC = 12.0
"""Cap on the position error used in match gates (keeps a badly measured source from matching anything)."""


def _cache() -> JsonCache:
    return JsonCache(get_settings().cache_dir / "catalogs", 7 * 86400)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def propagate_catalogue_source(src: dict[str, Any], mjd: float) -> tuple[float, float]:
    """Catalogue position at ``mjd`` (linear proper motion; suspect motions not applied)."""
    return cat.propagate(src, mjd)


# --------------------------------------------------------------------------
# SIMBAD
# --------------------------------------------------------------------------
def query_simbad(ra: float, dec: float, radius_deg: float, limit: int = 200) -> list[dict[str, Any]]:
    adql = (f"SELECT TOP {int(limit)} main_id, otype, ra, dec, pmra, pmdec, plx_value "
            f"FROM basic WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {radius_deg:.6f}))=1")
    last: Exception | None = None
    for url in SIMBAD_TAP:
        try:
            resp = request_with_retries(get_session(), "POST", url, timeout=(8, 30), retries=0, backoff=1.0,
                                        data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql})
            if "main_id" not in resp.text[:200]:
                raise RuntimeError("unexpected SIMBAD response")
            rows = list(csv.DictReader(io.StringIO(resp.text)))
            return [{"id": r["main_id"].strip(), "otype": r.get("otype"), "ra": _f(r["ra"]), "dec": _f(r["dec"]),
                     "ref_mjd": J2000_MJD, "pm_ra_cosdec_mas_yr": _f(r.get("pmra")), "pm_dec_mas_yr": _f(r.get("pmdec")),
                     "parallax_mas": _f(r.get("plx_value")), "mag": None, "mag_band": None}
                    for r in rows if _f(r["ra"]) is not None]
        except Exception as exc:  # try the mirror
            last = exc
            log.info("SIMBAD TAP %s failed: %s", url, exc)
    raise RuntimeError(f"SIMBAD unavailable ({type(last).__name__})")


# --------------------------------------------------------------------------
# SkyBoT
# --------------------------------------------------------------------------
def query_skybot(ra: float, dec: float, radius_deg: float, mjd: float, use_cache: bool = True) -> list[dict[str, Any]]:
    """Known solar-system objects in a cone at ``mjd`` (geocentric ephemerides, IMCCE SkyBoT)."""
    key = stable_hash({"skybot": 1, "ra": round(ra, 4), "dec": round(dec, 4), "r": round(radius_deg, 4),
                       "t": round(mjd, 3)})
    c = _cache()
    if use_cache and (hit := c.get(key)) is not None:
        return hit
    params = {"-ep": f"{mjd + 2400000.5:.5f}", "-ra": f"{ra:.6f}", "-dec": f"{dec:.6f}", "-rd": f"{radius_deg:.4f}",
              "-mime": "text", "-output": "basic", "-loc": "500", "-filter": "120", "-objFilter": "111"}
    resp = request_with_retries(get_session(), "GET", SKYBOT_URL, params=params, timeout=(8, 60), retries=1, backoff=1.0)
    out = parse_skybot(resp.text, mjd)
    c.set(key, out)
    return out


def parse_skybot(text: str, mjd: float) -> list[dict[str, Any]]:
    from astropy.coordinates import Angle

    out = []
    for line in text.splitlines():
        if line.startswith("#") or "|" not in line:
            continue
        f = [x.strip() for x in line.split("|")]
        if len(f) < 10:
            continue
        try:
            ra = Angle(f[2], unit="hourangle").deg
            dec = Angle(f[3], unit="deg").deg
        except Exception:
            continue
        out.append({"id": f[1], "number": None if f[0] in ("-", "") else f[0], "class": f[4],
                    "v_mag": _f(f[5]), "pos_err_arcsec": _f(f[6]), "ra": ra, "dec": dec, "mjd": mjd,
                    "rate_ra_cosdec_arcsec_h": _f(f[8]), "rate_dec_arcsec_h": _f(f[9]),
                    "geocentric_distance_au": _f(f[10]) if len(f) > 10 else None})
    return out


def skybot_position(obj: dict[str, Any], mjd: float) -> tuple[float, float]:
    """Short-term linear ephemeris from a SkyBoT position + rate (good for hours, not days)."""
    to_mas_yr = 1000 * 24 * DAYS_PER_YEAR  # arcsec/h -> mas/yr
    m = {"ra": obj["ra"], "dec": obj["dec"], "ref_mjd": obj["mjd"],
         "pm_ra_cosdec_mas_yr": (obj.get("rate_ra_cosdec_arcsec_h") or 0) * to_mas_yr,
         "pm_dec_mas_yr": (obj.get("rate_dec_arcsec_h") or 0) * to_mas_yr}
    return predict_position(m, mjd)


# --------------------------------------------------------------------------
# Field catalogues + per-source matching
# --------------------------------------------------------------------------
def field_catalogues(ra: float, dec: float, radius_deg: float,
                     catalogs: Iterable[str] = DEFAULT_CATALOGS) -> dict[str, dict[str, Any]]:
    """One cone query per catalogue for a whole field: ``{name: {"status", "sources", ...}}``."""
    out: dict[str, dict[str, Any]] = {}
    radius_deg = min(max(radius_deg, 1e-3), cat.MAX_RADIUS_DEG)
    for name in catalogs:
        try:
            if name in cat.CATALOGS:
                r = cat.cone_search(name, ra, dec, radius_deg, limit=2000)
                out[name] = {"status": "ok", "count": r["count"], "sources": r["sources"]}
            elif name == "simbad":
                key = stable_hash({"simbad": 1, "ra": round(ra, 5), "dec": round(dec, 5), "r": round(radius_deg, 5)})
                c = _cache()
                src = c.get(key)
                if src is None:
                    src = query_simbad(ra, dec, radius_deg)
                    c.set(key, src)
                out[name] = {"status": "ok", "count": len(src), "sources": src}
            else:
                out[name] = {"status": "error", "error": f"unknown catalogue {name}", "sources": []}
        except Exception as exc:
            log.warning("catalogue %s unavailable: %s", name, exc)
            out[name] = {"status": "unavailable", "error": f"{type(exc).__name__}", "sources": []}
    return out


def match_to_catalogue(position: dict[str, Any], sources: list[dict[str, Any]], *,
                       radius_arcsec: float = MATCH_RADIUS_ARCSEC) -> dict[str, Any] | None:
    """Best catalogue counterpart of a measured source, allowing for motion both ways.

    ``position``: ``ra``, ``dec``, ``mjd`` (and optionally ``pm_ra_cosdec_mas_yr``,
    ``pm_dec_mas_yr``, ``pm_err_mas_yr``, ``pos_err_arcsec``). Each catalogue
    source is compared (a) propagated with *its* proper motion to the
    measurement epoch and (b) against the measurement propagated with the
    *measured* motion back to the catalogue epoch (for catalogue entries
    without proper motion). The smaller separation wins.
    """
    if not sources:
        return None
    mjd = position["mjd"]
    pe = min(position.get("pos_err_arcsec") or 1.0, MAX_POS_ERR_ARCSEC)
    best = None
    for s in sources:
        if s.get("ra") is None:
            continue
        cra, cdec = propagate_catalogue_source(s, mjd)
        sep_a = float(separation_arcsec(cra, cdec, position["ra"], position["dec"]))
        gate_a = math.hypot(radius_arcsec, 3 * pe)
        sep, gate, how = sep_a, gate_a, "catalogue motion"
        if position.get("motion_significant") and s.get("ref_mjd") is not None and (
                s.get("pm_ra_cosdec_mas_yr") is None or s.get("pm_suspect")):
            mra, mdec = predict_position({"ra": position["ra"], "dec": position["dec"], "ref_mjd": mjd,
                                          "pm_ra_cosdec_mas_yr": position["pm_ra_cosdec_mas_yr"],
                                          "pm_dec_mas_yr": position["pm_dec_mas_yr"]}, s["ref_mjd"])
            sep_b = float(separation_arcsec(mra, mdec, s["ra"], s["dec"]))
            dt = abs(mjd - s["ref_mjd"]) / DAYS_PER_YEAR
            gate_b = min(math.hypot(radius_arcsec, 3 * math.hypot(pe, (position.get("pm_err_mas_yr") or 0) / 1000 * dt)),
                         MAX_GATE_ARCSEC)
            if sep_b / gate_b < sep_a / gate_a:
                sep, gate, how = sep_b, gate_b, "measured motion"
        if sep <= gate and (best is None or sep / gate < best["_score"]):
            best = {"_score": sep / gate, "id": s["id"], "separation_arcsec": round(sep, 3), "matched_using": how,
                    "catalog_ra": s["ra"], "catalog_dec": s["dec"], "catalog_ref_mjd": s.get("ref_mjd"),
                    "pm_ra_cosdec_mas_yr": s.get("pm_ra_cosdec_mas_yr"), "pm_dec_mas_yr": s.get("pm_dec_mas_yr"),
                    "pm_suspect": bool(s.get("pm_suspect")), "parallax_mas": s.get("parallax_mas"),
                    "mag": s.get("mag"), "mag_band": s.get("mag_band"), "otype": s.get("otype"),
                    "w1_mag": s.get("w1_mag"), "w2_mag":
                    s.get("mag") if s.get("mag_band") == "W2" else None}
    if best is None:
        return None
    best.pop("_score")
    n_within = sum(1 for s in sources if s.get("ra") is not None and
                   separation_arcsec(*propagate_catalogue_source(s, mjd), position["ra"], position["dec"])
                   <= math.hypot(radius_arcsec, 3 * pe))
    best["n_candidates_in_radius"] = int(n_within)
    # Measured vs catalogue proper motion
    if position.get("pm_ra_cosdec_mas_yr") is not None and best["pm_ra_cosdec_mas_yr"] is not None and not best["pm_suspect"]:
        d = math.hypot(position["pm_ra_cosdec_mas_yr"] - best["pm_ra_cosdec_mas_yr"],
                       position["pm_dec_mas_yr"] - best["pm_dec_mas_yr"])
        err = max(position.get("pm_err_mas_yr") or 0, 1.0)
        best["pm_difference_mas_yr"] = round(d, 1)
        best["pm_difference_sigma"] = round(d / err, 2)
        best["pm_consistent"] = bool(d / err < 3)
    return best


def crossmatch_catalogue(ra: float, dec: float, radius: float = MATCH_RADIUS_ARCSEC / 3600, *,
                         epoch: Any = None, catalogs: Iterable[str] = DEFAULT_CATALOGS,
                         include_solar_system: bool = False) -> dict[str, Any]:
    """Look up a position in Gaia DR3, CatWISE2020 and SIMBAD (and SkyBoT if asked).

    ``radius`` in degrees (default 4"). With ``epoch`` (MJD / ISO / Time),
    catalogue positions are propagated to it before measuring separations.
    Returns per-catalogue status and the matches sorted by separation. An
    empty result means "no counterpart in the catalogues that answered",
    never "new object".
    """
    mjd = parse_time(epoch).mjd if epoch is not None else None
    query_r = max(radius, 30 / 3600)  # fast movers may have drifted since the catalogue epoch
    fields = field_catalogues(ra, dec, query_r, catalogs)
    checked, matches = [], []
    for name, f in fields.items():
        checked.append({"catalog": name, "status": f["status"], **({"error": f["error"]} if "error" in f else {})})
        for s in f["sources"]:
            p = propagate_catalogue_source(s, mjd) if mjd is not None else (s["ra"], s["dec"])
            sep = float(separation_arcsec(p[0], p[1], ra, dec))
            if sep <= radius * 3600:
                matches.append({"catalog": name, "id": s["id"], "separation_arcsec": round(sep, 3),
                                "ra": p[0], "dec": p[1], "pm_ra_cosdec_mas_yr": s.get("pm_ra_cosdec_mas_yr"),
                                "pm_dec_mas_yr": s.get("pm_dec_mas_yr"), "mag": s.get("mag"),
                                "mag_band": s.get("mag_band"), "otype": s.get("otype")})
    if include_solar_system and mjd is not None:
        try:
            for o in query_skybot(ra, dec, max(radius, 0.01), mjd):
                sep = float(separation_arcsec(o["ra"], o["dec"], ra, dec))
                if sep <= max(radius * 3600, 3 * (o.get("pos_err_arcsec") or 0)):
                    matches.append({"catalog": "skybot", "id": o["id"], "separation_arcsec": round(sep, 3),
                                    "ra": o["ra"], "dec": o["dec"], "otype": o["class"], "mag": o["v_mag"], "mag_band": "V"})
            checked.append({"catalog": "skybot", "status": "ok"})
        except Exception as exc:
            checked.append({"catalog": "skybot", "status": "unavailable", "error": type(exc).__name__})
    matches.sort(key=lambda m: m["separation_arcsec"])
    unchecked = [c["catalog"] for c in checked if c["status"] != "ok"]
    note = ("Counterpart(s) found." if matches else
            "No counterpart in the catalogues that answered" + (f" (not checked: {', '.join(unchecked)})" if unchecked else "")
            + ". This does not make the source new: it may be below a catalogue's limit, blended, "
              "moved since the catalogue epoch, or an artefact.")
    return {"ra": ra, "dec": dec, "radius_arcsec": radius * 3600, "epoch_mjd": mjd, "checked": checked,
            "matches": matches, "note": note}
