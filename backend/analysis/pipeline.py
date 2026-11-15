"""End-to-end analysis of one processed SPHEREx time series.

``analyse_observations`` runs detection, temporal matching, motion fits,
forced photometry, variability, spectra, catalogue cross-matching and
classification on Person 2's processed observations, and returns one
record per source in the agreed output contract:

``{"source_id", "ra", "dec", "detections": [...], "angular_motion",
"motion_uncertainty", "variability_indicator", "classification",
"confidence", "known_catalogue_match"}`` plus supporting fields.

``analyse_field`` gets the observations through ``process_series`` (which
uses the data acquisition module) and caches the result next to the
processed series. ``to_viewer`` converts a result to the viewer's
``{sources, light_curves, spectra}`` overlay contract.
"""
from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Sequence

import numpy as np

from backend.data_acquisition.cache import stable_hash
from backend.data_acquisition.coords import ResolvedTarget
from backend.image_processing.pipeline import assign_epochs

from .classify import classify_candidate
from .crossmatch import DEFAULT_CATALOGS, field_catalogues, match_to_catalogue, query_skybot, skybot_position
from .detection import extract_source_photometry
from .matching import Track, match_sources
from .motion import calculate_angular_motion, predict_position, separation_arcsec
from .photometry import forced_photometry, generate_light_curve, generate_spectrum, variability_indicator

log = logging.getLogger(__name__)

ANALYSIS_VERSION = "1.1"
DISCLAIMER = ("Automated, preliminary analysis of SPHEREx data (releases listed in provenance). Classes are candidates "
              "defined by the listed criteria; none is a discovery. Anything unusual needs independent "
              "verification (other surveys, follow-up, expert inspection).")
CATALOGUE_PRIORITY = ("gaia", "catwise", "simbad")
HALO_ARTEFACT_ARCSEC = 40.0
TARGET_SEARCH_ARCSEC = 20.0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _clean(o: Any) -> Any:
    """JSON-safe copy (numpy scalars -> Python, NaN -> None, drop private keys)."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if not str(k).startswith("_") and k != "stack"}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    return o


def _detection_record(d: dict[str, Any], kind: str) -> dict[str, Any]:
    keys = ("observation_id", "mjd", "observation_time", "epoch", "wavelength", "detector", "ra", "dec",
            "ra_err_arcsec", "dec_err_arcsec", "registration_err_arcsec", "ra_err_total_arcsec",
            "dec_err_total_arcsec", "x", "y", "flux_mjy", "flux_err_mjy", "snr", "significance",
            "quality_flags", "quality", "n_frames")
    return {"kind": kind, **{k: d.get(k) for k in keys if k in d}}


def _target_offsets(track_points: list[dict[str, Any]], target: ResolvedTarget) -> list[float]:
    out = []
    for p in track_points:
        c = target.position_at(p["mjd"])
        out.append(float(separation_arcsec(c.ra.deg, c.dec.deg, p["ra"], p["dec"])))
    return out


def _best_match(matches: dict[str, dict[str, Any] | None]) -> dict[str, Any] | None:
    good = [(c, m) for c, m in matches.items() if m]
    if not good:
        return None
    consistent = [(c, m) for c, m in good if m.get("pm_consistent")]
    pool = consistent or good
    pool.sort(key=lambda cm: CATALOGUE_PRIORITY.index(cm[0]) if cm[0] in CATALOGUE_PRIORITY else 9)
    c, m = pool[0]
    return {"catalog": c, **m}


# --------------------------------------------------------------------------
# Per-source analysis
# --------------------------------------------------------------------------
def _analyse_track(t: Track, frame_dets: list[dict[str, Any]], observations: Sequence[Any], epochs: dict[str, int],
                   cats: dict[str, dict[str, Any]], visits: list[dict[str, Any]]) -> dict[str, Any]:
    pts = sorted(t.points, key=lambda p: p["mjd"])
    n_vis = len(pts)
    motion = t.motion() if n_vis >= 2 else None
    if motion and motion.get("ra_at_ref") is not None:
        ra, dec, ref_mjd = motion["ra_at_ref"], motion["dec_at_ref"], motion["ref_mjd"]
        pos_err = math.hypot(*motion.get("position_err_at_ref_arcsec", [1, 1])) / math.sqrt(2)
    else:
        p0 = pts[0]
        ra, dec, ref_mjd = p0["ra"], p0["dec"], p0["mjd"]
        pos_err = math.hypot(p0["ra_err_total_arcsec"], p0["dec_err_total_arcsec"]) / math.sqrt(2)

    # Catalogue matching at the reference epoch, allowing for motion.
    probe = {"ra": ra, "dec": dec, "mjd": ref_mjd, "pos_err_arcsec": pos_err}
    if motion and motion.get("pm_ra_cosdec_mas_yr") is not None:
        probe.update(pm_ra_cosdec_mas_yr=motion["pm_ra_cosdec_mas_yr"], pm_dec_mas_yr=motion["pm_dec_mas_yr"],
                     pm_err_mas_yr=motion["pm_err_mas_yr"], motion_significant=(motion.get("motion_sigma") or 0) >= 3)
    matches = {name: match_to_catalogue(probe, c["sources"]) if c["status"] == "ok" else None
               for name, c in cats.items()}
    best = _best_match(matches)
    # Refit with the catalogue parallax removed when it is large enough to matter.
    plx = next((m["parallax_mas"] for m in (matches.get("gaia"), matches.get("simbad"))
                if m and m.get("parallax_mas") and m["parallax_mas"] > 20), None)
    if motion and plx and n_vis >= 2:
        motion_plx = calculate_angular_motion([(p["ra"], p["dec"]) for p in pts], [p["mjd"] for p in pts],
                                              [(p["ra_err_total_arcsec"], p["dec_err_total_arcsec"]) for p in pts],
                                              parallax_mas=plx)
        if motion_plx.get("pm_ra_cosdec_mas_yr") is not None:
            motion_plx["without_parallax"] = {k: motion.get(k) for k in
                                              ("pm_ra_cosdec_mas_yr", "pm_dec_mas_yr", "linear_chi2_red")}
            motion = motion_plx
            probe.update(pm_ra_cosdec_mas_yr=motion["pm_ra_cosdec_mas_yr"], pm_dec_mas_yr=motion["pm_dec_mas_yr"],
                         pm_err_mas_yr=motion["pm_err_mas_yr"])
            matches = {name: match_to_catalogue(probe, c["sources"]) if c["status"] == "ok" else None
                       for name, c in cats.items()}
            best = _best_match(matches)

    mine = [d for d in frame_dets if d.get("track_id") == t.id]
    src: dict[str, Any] = {"source_id": t.id, "ra": ra, "dec": dec, "ref_mjd": ref_mjd,
                           "angular_motion": motion, "visit_positions": pts}
    phot = forced_photometry(src, observations, epochs)
    lc = generate_light_curve(t.id, observations, photometry=phot)
    var = variability_indicator(lc["points"])
    spec = generate_spectrum(t.id, lc["points"])

    sat_flags = {"masked_core", "saturated_core_raw_fit", "saturation_nearby"}
    saturated = sum(1 for p in pts if set(p["quality_flags"]) & sat_flags) >= max(1, n_vis / 2)
    qual = float(np.mean([p["quality"] for p in pts])) if pts else 0.0

    transient = None
    if n_vis == 1:
        transient = _transient_test(pts[0], visits)
    meas = {"n_visits": n_vis, "n_frame_detections": len(mine), "quality": qual, "saturated": saturated,
            "angular_motion": motion, "variability_indicator": var, "transient": transient,
            "known_catalogue_match": best, "catwise_match": matches.get("catwise"),
            "catalogues_checked": [{"catalog": k, "status": v["status"]} for k, v in cats.items()]}
    cls = classify_candidate(meas)

    motion_unc = None
    if motion and motion.get("pm_err_mas_yr") is not None:
        motion_unc = {"pm_ra_cosdec_err_mas_yr": motion["pm_ra_cosdec_err_mas_yr"],
                      "pm_dec_err_mas_yr": motion["pm_dec_err_mas_yr"], "pm_total_err_mas_yr": motion["pm_err_mas_yr"],
                      "position_err_at_ref_arcsec": motion.get("position_err_at_ref_arcsec"),
                      "registration_err_arcsec": [p.get("registration_err_arcsec") for p in pts],
                      "error_scale_applied": motion.get("error_scale")}
    return {
        "source_id": t.id, "ra": ra, "dec": dec, "ref_mjd": ref_mjd,
        "detections": [_detection_record(p, "visit_stack") for p in pts] + [_detection_record(d, "frame") for d in mine],
        "angular_motion": motion, "motion_uncertainty": motion_unc,
        "variability_indicator": var, "classification": cls["classification"], "label": cls["label"],
        "confidence": cls["confidence"], "status": cls["status"], "subtype": cls["subtype"],
        "secondary_flags": cls["secondary_flags"], "note": cls["note"],
        "known_catalogue_match": best, "catalogue_matches": matches,
        "criteria": cls["tests"], "saturated": saturated, "n_visits": n_vis, "n_frame_detections": len(mine),
        "transient_test": transient, "light_curve": lc, "spectrum": spec,
    }


def _transient_test(p: dict[str, Any], visits: list[dict[str, Any]]) -> dict[str, Any]:
    """Forced photometry at a single-visit source's position in the other visits' stacks."""
    others = [v for v in visits if v["epoch"] != p["epoch"] and v["detector"] == p["detector"]]
    snrs, fluxes = [], []
    for v in others:
        r = extract_source_photometry(v["stack"], [{"ra": p["ra"], "dec": p["dec"]}])[0]
        if r["flux_mjy"] is not None and not ({"off_frame", "masked_core"} & set(r["quality_flags"])):
            snrs.append(r["snr"] or 0.0)
            fluxes.append((r["flux_mjy"], r["flux_err_mjy"]))
    det_f = p.get("flux_mjy")
    factor = None
    if det_f and fluxes:
        ceiling = max(max(f + 2 * e, 3 * e) for f, e in fluxes)
        factor = det_f / ceiling if ceiling > 0 else None
    return {"detected_snr": p.get("snr") or p.get("significance"), "n_other_visits_covered": len(snrs),
            "max_other_visit_snr": max(snrs) if snrs else None, "flux_factor": factor}


def _analyse_tracklet(tl: dict[str, Any], field: dict[str, Any], use_skybot: bool) -> dict[str, Any]:
    ds = tl["detections"]
    mid = ds[len(ds) // 2]
    known, checked = None, []
    if use_skybot:
        try:
            objs = query_skybot(field["ra"], field["dec"], field["radius_deg"], tl["mjd_mid"])
            checked.append({"catalog": "skybot", "status": "ok"})
            best = None
            for o in objs:
                errs = []
                for d in ds:
                    pra, pdec = skybot_position(o, d["mjd"])
                    errs.append(float(separation_arcsec(pra, pdec, d["ra"], d["dec"])))
                med = float(np.median(errs))
                tol = 10.0 + 3 * (o.get("pos_err_arcsec") or 0)
                if med < tol and (best is None or med < best["separation_arcsec"]):
                    best = {"catalog": "skybot", "id": o["id"], "class": o["class"], "v_mag": o["v_mag"],
                            "separation_arcsec": round(med, 2)}
            known = best
        except Exception as exc:
            checked.append({"catalog": "skybot", "status": "unavailable", "error": type(exc).__name__})
    meas = {"n_visits": 1, "n_frame_detections": tl["n"], "tracklet": tl,
            "quality": float(np.mean([d["quality"] for d in ds])), "known_catalogue_match": known,
            "catalogues_checked": checked}
    cls = classify_candidate(meas)
    rate = {"rate_arcsec_h": tl["rate_arcsec_h"], "rate_ra_cosdec_arcsec_h": tl["rate_ra_cosdec_arcsec_h"],
            "rate_dec_arcsec_h": tl["rate_dec_arcsec_h"], "pa_deg": tl["pa_deg"], "duration_h": tl["duration_h"],
            "rms_arcsec": tl["rms_arcsec"], "model": "linear over the tracklet (apparent, non-sidereal)",
            # same convention as stars so the viewer can propagate it within the visit
            "pm_ra_cosdec_mas_yr": tl["rate_ra_cosdec_arcsec_h"] * 1000 * 24 * 365.25,
            "pm_dec_mas_yr": tl["rate_dec_arcsec_h"] * 1000 * 24 * 365.25, "ref_mjd": mid["mjd"],
            "ra_at_ref": mid["ra"], "dec_at_ref": mid["dec"]}
    return {"source_id": tl["id"], "ra": mid["ra"], "dec": mid["dec"], "ref_mjd": mid["mjd"],
            "detections": [_detection_record(d, "frame") for d in ds], "angular_motion": rate,
            "motion_uncertainty": {"tracklet_rms_arcsec": tl["rms_arcsec"]}, "variability_indicator": None,
            "classification": cls["classification"], "label": cls["label"], "confidence": cls["confidence"],
            "status": cls["status"], "subtype": None, "secondary_flags": cls["secondary_flags"], "note": cls["note"],
            "known_catalogue_match": known, "catalogue_matches": {"skybot": known}, "criteria": cls["tests"],
            "saturated": False, "n_visits": 1, "n_frame_detections": tl["n"], "light_curve": None, "spectrum": None}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def identify_moving_candidates(matched_sources: list[dict[str, Any]], include_known: bool = True) -> list[dict[str, Any]]:
    """Moving sources (high proper motion or solar-system rate), best first.

    ``matched_sources``: analysed source records (from ``analyse_observations``).
    Each returned record carries predicted positions at its detection times
    from its fitted motion model (proper motion or tracklet rate).
    """
    movers = [s for s in matched_sources
              if s["classification"] in ("high_proper_motion_candidate", "possible_solar_system_object")
              or "high_proper_motion_candidate" in (s.get("secondary_flags") or [])]
    if not include_known:
        movers = [s for s in movers if s["status"] != "known"]
    out = []
    for s in movers:
        m = s["angular_motion"] or {}
        pred = []
        if m.get("ra_at_ref") is not None:
            for d in s["detections"]:
                if d.get("mjd") is None:
                    continue
                pr, pd = predict_position(m, d["mjd"])
                pred.append({"observation_id": d["observation_id"], "mjd": d["mjd"], "kind": d["kind"],
                             "predicted_ra": pr, "predicted_dec": pd,
                             "residual_arcsec": float(separation_arcsec(pr, pd, d["ra"], d["dec"]))})
        out.append({**s, "predicted_positions": pred,
                    "motion_type": "solar-system (non-sidereal)" if s["classification"] == "possible_solar_system_object"
                    else ("linear proper motion" if m.get("model") == "linear" else m.get("model"))})
    out.sort(key=lambda s: -s["confidence"])
    return out


def analyse_observations(observations: Sequence[Any], *, ra: float, dec: float, fov: float,
                         target: ResolvedTarget | None = None, catalogs: Sequence[str] = DEFAULT_CATALOGS,
                         use_skybot: bool = True, nsigma: float = 5.0) -> dict[str, Any]:
    """Full analysis of processed observations of one field (see module docstring)."""
    t0 = time.time()
    obs = sorted([o for o in observations if o.mjd is not None], key=lambda o: o.mjd)
    if not obs:
        return {"sources": [], "summary": {"n_observations": 0}, "warnings": ["no observations"],
                "disclaimer": DISCLAIMER}
    radius = fov * 0.75 + 0.004
    cats = field_catalogues(ra, dec, radius, catalogs) if catalogs else {}
    gaia = cats.get("gaia", {}).get("sources") if cats.get("gaia", {}).get("status") == "ok" else None
    matched = match_sources(obs, reference_stars=gaia, nsigma=nsigma)
    epochs = assign_epochs(obs)
    sources = [_analyse_track(t, matched["frame_detections"], obs, epochs, cats, matched["visits"])
               for t in matched["tracks"]]
    field = {"ra": ra, "dec": dec, "fov_deg": fov, "radius_deg": radius}
    sources += [_analyse_tracklet(tl, field, use_skybot) for tl in matched["tracklets"]]

    # Sources in the halo of a saturated star without a catalogue counterpart are
    # most likely PSF-wing / bloom / ghost artefacts.
    sat = [s for s in sources if s.get("saturated")]
    for s in sources:
        if s.get("known_catalogue_match"):
            continue
        near = [o for o in sat if o is not s and separation_arcsec(o["ra"], o["dec"], s["ra"], s["dec"]) < HALO_ARTEFACT_ARCSEC]
        if near or s.get("saturated"):
            why = (f"within {HALO_ARTEFACT_ARCSEC:.0f}\" of saturated {near[0]['source_id']}" if near
                   else "sits on saturation-flagged pixels")
            s["secondary_flags"] = list(s.get("secondary_flags") or []) + ["saturation_artefact_risk"]
            s["artefact_risk"] = True
            s["confidence"] = round(s["confidence"] * 0.5, 3)
            s["note"] = (f"Unmatched and {why}: probably a PSF-wing, bloom or ghost artefact. " + s["note"])

    # Which source is the requested target / the source at the field centre?
    mid = float(np.median([o.mjd for o in obs]))
    target_info = None
    if target is not None:
        best = None
        for s in sources:
            pts = [d for d in s["detections"] if d["kind"] == "visit_stack"]
            if not pts:
                continue
            offs = _target_offsets(pts, target)
            med = float(np.median(offs))
            if med < TARGET_SEARCH_ARCSEC and (best is None or med < best[1]):
                best = (s, med, offs)
        if best:
            best[0]["is_target"] = True
            m = best[0]["angular_motion"] or {}
            target_info = {"name": target.name, "source_id": best[0]["source_id"],
                           "reliable_astrometry": not best[0].get("saturated") or best[1] < 3.0,
                           "median_offset_from_prediction_arcsec": round(best[1], 3),
                           "offsets_per_visit_arcsec": [round(x, 3) for x in best[2]],
                           "catalogue_pm_mas_yr": [target.pm_ra_cosdec_mas_yr, target.pm_dec_mas_yr],
                           "measured_pm_mas_yr": [m.get("pm_ra_cosdec_mas_yr"), m.get("pm_dec_mas_yr")],
                           "measured_pm_err_mas_yr": [m.get("pm_ra_cosdec_err_mas_yr"), m.get("pm_dec_err_mas_yr")]}
        else:
            target_info = {"name": target.name, "source_id": None,
                           "note": f"Target not detected within {TARGET_SEARCH_ARCSEC:.0f}\" of its predicted position (faint, saturated or masked)."}
    centre = None
    for s in sources:
        m = s["angular_motion"] or {}
        try:
            pr, pd = predict_position(m, mid) if m.get("ra_at_ref") is not None and s["classification"] != "possible_solar_system_object" else (s["ra"], s["dec"])
        except ValueError:
            pr, pd = s["ra"], s["dec"]
        sep = float(separation_arcsec(pr, pd, ra, dec))
        if sep < 12 and (centre is None or sep < centre[1]):
            centre = (s, sep)
    if centre:
        centre[0]["is_field_centre"] = True

    def rank(s):
        cand = s["classification"] not in ("stationary", "insufficient_data")
        return (not s.get("is_target"), not s.get("is_field_centre"), not cand, -s["confidence"])
    sources.sort(key=rank)
    counts: dict[str, int] = {}
    for s in sources:
        counts[s["classification"]] = counts.get(s["classification"], 0) + 1
    warnings = []
    cal = matched["registration_calibration"]
    if not any(v["registration"]["applied"] for v in matched["visits"]) and cal["method"] == "default floor":
        warnings.append("Registration could not be measured (too few Gaia or stationary field stars); "
                        f"a {cal['sigma_arcsec']}\" per-visit floor is assumed in all position errors.")
    n_art = sum(1 for s in sources if s.get("artefact_risk"))
    if n_art >= 3 and n_art >= len(sources) / 2:
        warnings.append(f"{n_art} of {len(sources)} sources look like saturation artefacts: the field is dominated "
                        "by a saturated star, so detections here are unreliable. Try shorter-wavelength detectors.")
    for name, c in cats.items():
        if c["status"] != "ok":
            warnings.append(f"Catalogue {name} unavailable ({c.get('error')}); sources were not checked against it.")
    rels = sorted({o.data_release for o in obs if o.data_release})
    if len(rels) > 1:
        from .photometry import UNIFORM_RELEASE_SET
        warnings.append(f"The series combines releases {', '.join(rels)}. " + (
            "IRSA documents DR1 + QR3 as one uniformly processed set." if set(rels) <= UNIFORM_RELEASE_SET else
            "These were calibrated differently; variability across releases is not tested."))
    wsrc = sorted({str(o.processing.get("wavelength_source")) for o in obs})
    if any("CWAVE" not in w for w in wsrc):
        warnings.append("Some frames use the approximate WCS-WAVE lookup (CWAVE/CBAND calibration unavailable "
                        "or mismatched): wavelengths good to ~0.003 um.")
    tgt_src = next((s for s in sources if s.get("is_target")), None)
    if tgt_src is not None and tgt_src.get("saturated"):
        warnings.append(f"The target ({tgt_src['source_id']}) is saturated at these wavelengths: its position comes "
                        "from RAW centroids and it has no reliable photometry, light curve or spectrum.")
    if len({o.detector for o in obs}) > 1:
        warnings.append("Several detectors: sources are tracked per detector.")
    visits = [{"epoch": v["epoch"], "detector": v["detector"], "mjd": v["mjd"], "mjd_range": v["mjd_range"],
               "n_frames": v["n_frames"], "wavelength_range": v["wavelength_range"], "releases": v["releases"],
               "n_detections": len(v["detections"]), "registration": v["registration"]} for v in matched["visits"]]
    return _clean({
        "analysis_version": ANALYSIS_VERSION, "field": field, "target": target_info,
        "provenance": {"releases": rels, "wavelength_source": wsrc,
                       "wavelength_calibration": sorted({str(o.processing.get("wavelength_calibration")) for o in obs}),
                       "pipeline_versions": sorted({str(o.processing.get("pipeline_version")) for o in obs}),
                       "psf_header_fix_needed": any(o.processing.get("psf_header_fix_needed") for o in obs),
                       "psf_model": "pixel-integrated circular Gaussian, FWHM from PSF_FWHM header (EPSF not used)"},
        "summary": {"n_observations": len(obs), "n_visits": len(visits), "n_sources": len(sources),
                    "n_frame_detections": len(matched["frame_detections"]), "n_orphan_detections": len(matched["orphans"]),
                    "classes": counts, "runtime_s": round(time.time() - t0, 2)},
        "visits": visits, "registration_calibration": matched["registration_calibration"],
        "catalogues": {k: {"status": v["status"], "count": v.get("count", 0), **({"error": v["error"]} if "error" in v else {})}
                       for k, v in cats.items()},
        "sources": sources, "warnings": warnings, "disclaimer": DISCLAIMER,
    })


def analyse_field(ra: float, dec: float, fov: float = 0.05, *, target: ResolvedTarget | None = None,
                  detectors: Sequence[int] | None = (6,), use_cache: bool = True, catalogs: Sequence[str] = DEFAULT_CATALOGS,
                  use_skybot: bool = True, **series_kwargs: Any) -> dict[str, Any]:
    """Process (via Person 2's ``process_series``) and analyse one field; cached per series."""
    from backend.image_processing import process_series

    series = process_series(ra, dec, fov, detectors=detectors, make_gifs=False, **series_kwargs)
    from backend.image_processing.pipeline import PROCESSING_VERSION

    # Keyed by everything the result depends on, incl. the processing version (a reprocessed
    # series, e.g. new wavelength calibration, must not reuse an old analysis).
    key = stable_hash({"v": ANALYSIS_VERSION, "pv": PROCESSING_VERSION,
                       "obs": sorted(p["observation_id"] for p in series.products),
                       "t": target.name if target else None, "c": list(catalogs), "sb": use_skybot})
    path = series.series_dir / f"analysis_{key}.json"
    if use_cache and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    res = analyse_observations(series.observations, ra=ra, dec=dec, fov=fov, target=target,
                               catalogs=catalogs, use_skybot=use_skybot)
    res["series"] = {"n_processed": len(series.observations), "errors": len(series.errors),
                     "skipped": len(series.skipped), "detectors": sorted({p.detector for p in series.observations})}
    try:
        path.write_text(json.dumps(res), encoding="utf-8")
    except OSError as exc:
        log.warning("could not cache analysis: %s", exc)
    return res


# --------------------------------------------------------------------------
# Viewer contract
# --------------------------------------------------------------------------
def to_viewer(result: dict[str, Any], max_curves: int = 4) -> dict[str, Any]:
    """``{sources: SourceMarker[], light_curves: LightCurve[], spectra: Spectrum[]}`` for the viewer."""
    markers, curves, spectra = [], [], []
    for s in result.get("sources", []):
        m = s.get("angular_motion") or {}
        moving = s["classification"] in ("high_proper_motion_candidate", "possible_solar_system_object") or (
            (m.get("motion_sigma") or 0) >= 3)
        kind = "artifact" if s.get("artefact_risk") else "known" if s["status"] == "known" else (
            "candidate" if s["classification"] not in ("stationary", "insufficient_data") else "source")
        tag = (" ★" if s.get("is_target") else "")
        mk = {"id": s["source_id"], "label": f"{s['source_id']} {s['label']}{tag}",
              "ra": m.get("ra_at_ref", s["ra"]) if moving else s["ra"],
              "dec": m.get("dec_at_ref", s["dec"]) if moving else s["dec"],
              "mjd": s["ref_mjd"], "kind": kind, "score": s["confidence"],
              "notes": s["note"]}
        if moving and m.get("pm_ra_cosdec_mas_yr") is not None:
            mk["pm_ra_cosdec_mas_yr"] = m["pm_ra_cosdec_mas_yr"]
            mk["pm_dec_mas_yr"] = m["pm_dec_mas_yr"]
            if m.get("pm_total_mas_yr") is not None:
                mk["notes"] = (f"µ = {m['pm_total_mas_yr'] / 1000:.2f} ± {(m.get('pm_err_mas_yr') or 0) / 1000:.2f}\"/yr · "
                               + mk["notes"])
        if s["classification"] == "possible_solar_system_object":
            mk["positions"] = [{"observation_id": d["observation_id"], "ra": d["ra"], "dec": d["dec"]}
                               for d in s["detections"] if d["kind"] == "frame"]
        markers.append(mk)
    # Light curves / spectra: the target or field-centre source first, then candidates.
    chosen = [s for s in result.get("sources", []) if s.get("light_curve") and s["light_curve"]["points"]]
    chosen.sort(key=lambda s: (not s.get("is_target"), not s.get("is_field_centre"),
                               s["classification"] in ("stationary", "insufficient_data"), -s["confidence"]))
    for s in chosen[:max_curves]:
        curves.append({"source_id": s["source_id"], "label": f"{s['source_id']} {s['label']}", "units": "mJy",
                       "points": [{"mjd": p["mjd"], "flux": p["flux"], "flux_err": p["flux_err"],
                                   "wavelength": p["wavelength"], "observation_id": p["observation_id"]}
                                  for p in s["light_curve"]["points"]]})
    if chosen:
        s = chosen[0]
        for v in s["spectrum"].get("visits", []):
            if len(v["points"]) < 2:
                continue
            date = ""
            if v.get("mjd"):
                from astropy.time import Time
                date = Time(v["mjd"], format="mjd").iso[:10]
            spectra.append({"source_id": s["source_id"], "label": f"{s['source_id']} visit {v['epoch'] + 1} {date}".strip(),
                            "units": "mJy", "points": [{"wavelength": p["wavelength"], "flux": p["flux"],
                                                        "flux_err": p["flux_err"]} for p in v["points"]]})
    return {"sources": markers, "light_curves": curves, "spectra": spectra}


_MOTION_KEYS = ("model", "baseline_yr", "n_points", "pm_ra_cosdec_mas_yr", "pm_dec_mas_yr", "pm_total_mas_yr",
                "pm_ra_cosdec_err_mas_yr", "pm_dec_err_mas_yr", "pm_err_mas_yr", "pm_pa_deg", "pm_snr", "motion_sigma",
                "linear_chi2_red", "displacement_arcsec", "displacement_err_arcsec", "parallax_removed_mas",
                "ra_at_ref", "dec_at_ref", "position_err_at_ref_arcsec", "nonlinear_p_value")
_VAR_KEYS = ("sigma", "p_value", "amplitude", "amplitude_mag", "reliable", "significant", "n_visits",
             "wavelength_overlap", "min_visit_snr", "reason", "release_note", "warning")
_MATCH_KEYS = ("catalog", "id", "separation_arcsec", "pm_ra_cosdec_mas_yr", "pm_dec_mas_yr", "pm_difference_sigma",
               "pm_consistent", "mag", "mag_band", "otype", "matched_using")


def summarise_source(s: dict[str, Any]) -> dict[str, Any]:
    """One source without its per-detection arrays: what a results table or panel needs."""
    m = s.get("angular_motion") or {}
    v = s.get("variability_indicator") or {}
    km = s.get("known_catalogue_match") or None
    primary = (s.get("criteria") or {}).get(s.get("classification")) or {}
    return {
        "source_id": s["source_id"], "ra": s["ra"], "dec": s["dec"], "ref_mjd": s.get("ref_mjd"),
        "classification": s["classification"], "label": s["label"], "confidence": s["confidence"],
        "status": s["status"], "subtype": s.get("subtype"), "note": s.get("note"),
        "is_target": bool(s.get("is_target")), "is_field_centre": bool(s.get("is_field_centre")),
        "saturated": bool(s.get("saturated")), "artefact_risk": bool(s.get("artefact_risk")),
        "secondary_flags": s.get("secondary_flags") or [],
        "n_visits": s.get("n_visits"), "n_frame_detections": s.get("n_frame_detections"),
        "angular_motion": {k: m.get(k) for k in _MOTION_KEYS if k in m} or None,
        "variability_indicator": {k: v.get(k) for k in _VAR_KEYS if k in v} or None,
        "known_catalogue_match": {k: km.get(k) for k in _MATCH_KEYS if k in km} if km else None,
        "criteria": primary.get("criteria", []),
        "has_light_curve": bool((s.get("light_curve") or {}).get("points")),
        "has_spectrum": bool((s.get("spectrum") or {}).get("visits")),
    }


def find_source(result: dict[str, Any], source_id: str) -> dict[str, Any]:
    """The full record of one source in an analysis result (KeyError if absent)."""
    for s in result.get("sources", []):
        if s["source_id"].upper() == source_id.upper():
            return s
    raise KeyError(f"No source {source_id!r} in this field; known ids: "
                   + ", ".join(s["source_id"] for s in result.get("sources", [])))
