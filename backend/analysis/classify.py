"""Preliminary classification with measurable criteria (sections C and G).

Every class is decided by explicit tests on measured quantities. Each
test gives a score in [0, 1] (0 at its threshold, 1 at its "strong" value,
linear in between). The confidence is the weighted mean of the scores of
the class's tests, times a data-quality factor; a class is assigned only
when all of its *required* tests pass. The full list of tests, values and
scores is returned, so every number can be checked.

Classes (priority order when several apply):
``possible_solar_system_object`` > ``transient_candidate`` >
``high_proper_motion_candidate`` > ``variable_candidate`` > ``stationary``
(> ``insufficient_data``).

Status (separate from class): ``known`` when a catalogue counterpart
agrees with the measurement, ``candidate`` otherwise. Nothing here is a
discovery; ``candidate`` means "needs independent verification".
"""
from __future__ import annotations

import math
from typing import Any

HPM_MIN_MAS_YR = 500.0
"""Conventional high-proper-motion threshold (0.5"/yr, Luyten)."""
MOTION_SIGMA_MIN = 3.0
"""Required motion significance; the score rises to 1 at 15 sigma, so 3-5 sigma movers get low confidence."""
PM_SNR_MIN = 3.0
MIN_DISPLACEMENT_ARCSEC = 2.0
"""~1/3 pixel: below this, undersampling and registration systematics dominate."""
SSO_RATE_MIN_ARCSEC_H = 0.5
TRANSIENT_MIN_SNR = 10.0
TRANSIENT_MAX_OTHER_SNR = 3.0
TRANSIENT_MIN_FACTOR = 3.0


def _score(value: float | None, threshold: float, strong: float) -> float:
    if value is None or not math.isfinite(value):
        return 0.0
    if strong == threshold:
        return 1.0 if value >= threshold else 0.0
    return float(min(1.0, max(0.0, (value - threshold) / (strong - threshold))))


def _crit(name: str, value: Any, threshold: Any, strong: Any, weight: float, required: bool = True,
          lower_is_better: bool = False, unit: str = "") -> dict[str, Any]:
    if value is None:
        passed, sc = False, 0.0
    elif lower_is_better:
        passed = value <= threshold
        sc = _score(-value, -threshold, -strong)
    else:
        passed = value >= threshold
        sc = _score(value, threshold, strong)
    return {"name": name, "value": None if value is None else round(float(value), 4), "threshold": threshold,
            "strong": strong, "unit": unit, "passed": bool(passed), "score": round(sc, 3), "weight": weight,
            "required": required}


def _confidence(criteria: list[dict[str, Any]], quality: float) -> float:
    w = sum(c["weight"] for c in criteria)
    if w == 0:
        return 0.0
    return round(sum(c["score"] * c["weight"] for c in criteria) / w * (0.5 + 0.5 * quality), 3)


def classify_candidate(source_measurements: dict[str, Any]) -> dict[str, Any]:
    """Classify one source from its measurements.

    Expected keys (all optional): ``n_visits``, ``n_frame_detections``,
    ``quality`` (0-1, fraction of clean detections), ``saturated`` (bool),
    ``angular_motion`` (from ``calculate_angular_motion``),
    ``variability_indicator``, ``tracklet`` (from ``find_tracklets``),
    ``transient`` ({``detected_snr``, ``max_other_visit_snr``,
    ``flux_factor``, ``n_other_visits_covered``}), ``known_catalogue_match``
    and ``catalogues_checked``.
    """
    m = source_measurements
    q = float(m.get("quality", 1.0))
    n_vis = int(m.get("n_visits") or 0)
    tests: dict[str, list[dict[str, Any]]] = {}

    tl = m.get("tracklet")
    if tl:
        tests["possible_solar_system_object"] = [
            _crit("tracklet_detections", tl["n"], 3, 6, 1.0, unit="frames"),
            _crit("rate", tl["rate_arcsec_h"], SSO_RATE_MIN_ARCSEC_H, 5.0, 1.0, unit="arcsec/h"),
            _crit("tracklet_rms", tl["rms_arcsec"], 2.5, 0.8, 1.0, lower_is_better=True, unit="arcsec"),
        ]

    tr = m.get("transient")
    if tr and n_vis == 1:
        tests["transient_candidate"] = [
            _crit("detected_snr", tr.get("detected_snr"), TRANSIENT_MIN_SNR, 30.0, 1.0),
            _crit("other_visits_covered", tr.get("n_other_visits_covered"), 1, 3, 1.0, unit="visits"),
            _crit("max_snr_in_other_visits", tr.get("max_other_visit_snr"), TRANSIENT_MAX_OTHER_SNR, 0.0, 1.0,
                  lower_is_better=True),
            _crit("flux_factor_vs_other_visits", tr.get("flux_factor"), TRANSIENT_MIN_FACTOR, 10.0, 1.0),
            _crit("frame_detections", m.get("n_frame_detections"), 2, 6, 0.5, unit="frames"),
        ]

    mo = m.get("angular_motion") or {}
    if mo.get("pm_total_mas_yr") is not None:
        tests["high_proper_motion_candidate"] = [
            _crit("pm_total", mo["pm_total_mas_yr"], HPM_MIN_MAS_YR, 2000.0, 1.0, unit="mas/yr"),
            _crit("motion_significance", mo.get("motion_sigma"), MOTION_SIGMA_MIN, 15.0, 1.5, unit="sigma"),
            _crit("pm_snr", mo.get("pm_snr"), PM_SNR_MIN, 20.0, 1.0),
            _crit("displacement", mo.get("displacement_arcsec"), MIN_DISPLACEMENT_ARCSEC, 6.0, 1.0, unit="arcsec"),
            # Two visits give no consistency check; saturated centroids need one.
            _crit("visits", n_vis, 3 if m.get("saturated") else 2, 4, 1.0, unit="visits"),
            _crit("linear_fit_chi2_red", mo.get("linear_chi2_red") if mo.get("linear_chi2_red") is not None else 1.0,
                  5.0, 1.5, 0.5, required=False, lower_is_better=True),
        ]

    var = m.get("variability_indicator") or {}
    if var.get("p_value") is not None:
        tests["variable_candidate"] = [
            _crit("variability_sigma", var.get("sigma"), 3.3, 8.0, 1.5, unit="sigma"),
            _crit("amplitude", var.get("amplitude"), 3 * var.get("calibration_floor", 0.03), 0.5, 1.0,
                  unit="fractional"),
            _crit("wavelength_overlap", var.get("wavelength_overlap"), 0.3, 0.8, 1.0),
            _crit("visits", var.get("n_visits"), 2, 4, 0.5, unit="visits"),
            _crit("min_visit_flux_snr", var.get("min_visit_snr"), 5.0, 20.0, 1.0),
            _crit("not_saturated", 0.0 if m.get("saturated") else 1.0, 1.0, 1.0, 0.5),
        ]

    results = {}
    for cls, crit in tests.items():
        req_ok = all(c["passed"] for c in crit if c["required"])
        results[cls] = {"passed": req_ok, "confidence": _confidence(crit, q) if req_ok else 0.0, "criteria": crit}

    order = ["possible_solar_system_object", "transient_candidate", "high_proper_motion_candidate",
             "variable_candidate"]
    primary = next((c for c in order if results.get(c, {}).get("passed")), None)
    flags = [c for c in order if results.get(c, {}).get("passed") and c != primary]
    if primary is None:
        if n_vis >= 2 or (tl is None and n_vis >= 1 and m.get("n_frame_detections", 0) >= 3 and not tr):
            primary = "stationary" if n_vis >= 2 else "insufficient_data"
        else:
            primary = "insufficient_data"
        if primary == "stationary":
            # Confidence that it is static: motion test not significant, enough visits, clean data.
            crit = [_crit("visits", n_vis, 2, 4, 1.0, unit="visits"),
                    _crit("motion_significance", mo.get("motion_sigma") if mo else 0.0, 5.0, 1.0, 1.0,
                          lower_is_better=True, unit="sigma"),
                    _crit("pm_total", mo.get("pm_total_mas_yr") if mo else 0.0, HPM_MIN_MAS_YR, 100.0, 0.5,
                          lower_is_better=True, required=False, unit="mas/yr")]
            results["stationary"] = {"passed": True, "confidence": _confidence(crit, q), "criteria": crit}
        else:
            results["insufficient_data"] = {"passed": True, "confidence": 0.0, "criteria": []}

    # A saturated core is centroided on RAW: a "moving" or "variable" saturated source
    # without any catalogue counterpart is far more likely an artefact than real.
    if m.get("saturated") and not m.get("known_catalogue_match") and primary not in ("stationary", "insufficient_data"):
        flags = [primary] + flags
        primary = "insufficient_data"
        results["insufficient_data"] = {"passed": True, "confidence": 0.0, "criteria": [],
                                        "reason": "saturated core without catalogue counterpart: astrometry/photometry unreliable"}
    conf = results[primary]["confidence"]
    # Known vs candidate
    match = m.get("known_catalogue_match")
    checked = m.get("catalogues_checked") or []
    unchecked = [c["catalog"] for c in checked if c.get("status") != "ok"]
    if match:
        status = "known"
        if primary == "high_proper_motion_candidate":
            if match.get("pm_consistent"):
                note = f"Motion agrees with {match['id']} ({match.get('pm_difference_sigma')} sigma): known high-proper-motion object."
            elif match.get("pm_consistent") is False:
                status = "candidate"
                note = (f"Position matches {match['id']} but the measured motion differs by "
                        f"{match.get('pm_difference_sigma')} sigma: check for a mismatch or a bad measurement.")
            else:
                note = f"Matches {match['id']} (catalogue has no reliable proper motion to compare)."
        else:
            note = f"Counterpart {match['id']} at {match['separation_arcsec']}\"."
    else:
        status = "candidate"
        note = ("No counterpart in the catalogues that answered"
                + (f" (unchecked: {', '.join(unchecked)})" if unchecked else "")
                + ". Not a discovery claim: requires independent verification.")
    label = {"high_proper_motion_candidate": "high proper motion", "possible_solar_system_object": "possible solar-system object",
             "transient_candidate": "transient candidate", "variable_candidate": "variable candidate",
             "stationary": "stationary", "insufficient_data": "insufficient data"}[primary]
    if status == "known" and primary == "high_proper_motion_candidate" and match and match.get("pm_consistent"):
        label = "known high-proper-motion object"
    sub = None
    cw = m.get("catwise_match") or (match if match and match.get("w2_mag") is not None else None)
    if primary == "high_proper_motion_candidate" and cw:
        w1, w2 = cw.get("w1_mag"), cw.get("w2_mag")
        if w1 is not None and w2 is not None and w1 - w2 >= 1.0:
            sub = "possible_brown_dwarf (CatWISE W1-W2 >= 1.0)"
    return {"classification": primary, "label": label, "confidence": conf, "status": status, "subtype": sub,
            "secondary_flags": flags, "note": note, "tests": results}
