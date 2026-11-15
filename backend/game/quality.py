"""Automated quality control of a contribution.

These checks test whether a submission is complete, uses real observations of the right place,
and is internally and physically consistent. They are *not* scientific validation: a submission
that passes QA is a well-formed piece of evidence, nothing more. In particular a moving source
that matches nothing in JPL Horizons is not a discovery, and the engine never calls it one.

Physical references used:
  * the investigation's own list of real SPHEREx observations (ids, times, quality flags);
  * the catalogue position (Gaia DR3, epoch J2016.0, or SIMBAD J2000) and Gaia proper motion,
    which predict where a star should be at each observation time;
  * SPHEREx's 6.2 arcsec pixel scale, as the floor of any positional tolerance.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

PIXEL_ARCSEC = 6.2
CLASS_LABELS = {"ob", "a", "f", "g", "k", "m", "bd", "wd", "giant", "yso", "galaxy", "artefact", "not_source", "unsure"}
SPECTRAL_FEATURES = {"h2o_1_4", "h2o_1_9", "ch4_2_2", "ch4_3_3", "co_2_3", "co_4_6", "co2_4_3", "h2o_ice_3_0", "pah_3_3", "none"}
ARTEFACTS = {"ghost", "saturation", "bleed", "cosmic_ray", "satellite", "edge", "other"}


def _year(t: str | None) -> float | None:
    if not t:
        return None
    try:
        d = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    start = datetime(d.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(d.year + 1, 1, 1, tzinfo=timezone.utc)
    return d.year + (d - start).total_seconds() / (end - start).total_seconds()


def _sep_arcsec(ra1, de1, ra2, de2) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, de1, ra2, de2))
    a = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a)))) * 3600


def _offset_arcsec(ra1, de1, ra2, de2) -> tuple[float, float]:
    """Tangent-plane offset (east, north) of point 2 from point 1, arcsec."""
    dra = ((ra2 - ra1 + 180) % 360) - 180
    return dra * math.cos(math.radians((de1 + de2) / 2)) * 3600, (de2 - de1) * 3600


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.missing: list[str] = []
        self.warnings: list[str] = []
        self.facts: dict[str, Any] = {}

    def check(self, key: str, ok: bool | None, detail: str, severity: str = "error") -> bool | None:
        """ok=None means the check could not be run (recorded, not counted against the player)."""
        self.checks.append({"check": key, "result": "pass" if ok else "skipped" if ok is None else "fail",
                            "severity": severity, "detail": detail})
        if ok is False:
            {"error": self.errors, "missing": self.missing, "warning": self.warnings}[severity].append(f"{key}: {detail}")
        return ok


def run(inv: dict[str, Any], payload: dict[str, Any], *, duplicate: bool, cfg: dict[str, Any]) -> dict[str, Any]:
    """Return {status, checks, errors, warnings, missing, evidence_score, label, facts...}."""
    r = Report()
    cat = inv["category"]
    data = inv.get("data") or {}
    inv_obs = {o["id"]: o for o in (inv.get("observations") or [])}

    r.check("not_duplicate", not duplicate, "you already submitted this investigation" if duplicate else "first submission")

    # --- observation references ---------------------------------------------------------------
    used = [str(x) for x in (payload.get("observations") or [])]
    for m in payload.get("measurements") or []:
        if m.get("obs_id") and str(m["obs_id"]) not in used:
            used.append(str(m["obs_id"]))
    for f in payload.get("findings") or []:
        if f.get("obs_id") and str(f["obs_id"]) not in used:
            used.append(str(f["obs_id"]))
    if cat == "spectral" and payload.get("spectrum_measured") and not used:
        used = list(inv_obs)   # the spectrum endpoint measures the source in every SPHEREx image of it
    if not inv_obs:
        r.check("observations_available", None, "no SPHEREx observation list is attached to this investigation", "warning")
    elif r.check("observations_cited", bool(used), "cite at least one observation you examined" if not used else f"{len(used)} cited", "missing"):
        unknown = [o for o in used if o not in inv_obs]
        r.check("observations_real", not unknown,
                f"not observations of this field: {', '.join(unknown[:3])}" if unknown else "all cited observations belong to this field")
        times = [inv_obs[o].get("time") for o in used if o in inv_obs]
        bad = [t for t in times if _year(t) is None]
        r.check("timestamps_valid", not bad, "unparseable observation times" if bad else "observation times valid")
        flagged = [o for o in used if o in inv_obs and inv_obs[o].get("quality_flags")]
        r.check("image_quality_info", not flagged or bool(payload.get("notes")),
                f"{len(flagged)} cited frames carry archive quality flags; explain in notes how you handled them" if flagged
                else "no archive quality flags on cited frames", "warning")
    r.facts["observations_used"] = used

    label = None
    # --- category-specific ----------------------------------------------------------------------
    if cat == "classification":
        label = payload.get("label")
        if r.check("label_given", label is not None, "choose a class", "missing"):
            r.check("label_valid", label in CLASS_LABELS, f"'{label}' is not a known class")
        conf = payload.get("confidence")
        r.check("confidence_given", isinstance(conf, (int, float)) and 0 <= conf <= 1, "confidence must be 0–1", "missing")
        if inv.get("training") and label in CLASS_LABELS:
            ans = data.get("training_answer")
            r.facts["training_correct"] = (label == ans)
            r.facts["training_answer"] = ans

    elif cat == "spectral":
        feats = payload.get("features")
        if r.check("features_given", isinstance(feats, list) and len(feats) > 0, "list the features you saw, or 'none'", "missing"):
            bad = [f for f in feats if f not in SPECTRAL_FEATURES]
            r.check("features_valid", not bad, f"unknown features: {bad}" if bad else "features recognised")
        label = payload.get("label")
        if label is not None:
            r.check("label_valid", label in CLASS_LABELS, f"'{label}' is not a known class")
        r.check("spectrum_examined", bool(payload.get("spectrum_measured")), "measure or open the SPHEREx spectrum first", "missing")

    elif cat == "image_quality":
        findings = payload.get("findings") or []
        clean = bool(payload.get("clean"))
        r.check("finding_given", clean or bool(findings), "flag an artefact or mark the frames clean", "missing")
        r.check("consistent_answer", not (clean and findings), "cannot be both clean and contain artefacts")
        bad = [f for f in findings if f.get("artefact") not in ARTEFACTS]
        if findings:
            r.check("artefact_types_valid", not bad, "unknown artefact type" if bad else "artefact types recognised")
            no_obs = [f for f in findings if not f.get("obs_id")]
            r.check("findings_located", not no_obs, "each artefact needs the observation it is in", "missing")
        label = "clean" if clean else ("artefact" if findings else None)

    elif cat == "moving":
        label = _moving(inv, payload, inv_obs, r, cfg)

    # --- scoring of the evidence --------------------------------------------------------------
    counted = [c for c in r.checks if c["result"] != "skipped"]
    passed = sum(c["result"] == "pass" for c in counted)
    base = passed / len(counted) if counted else 0.0
    breadth = min(1.0, len(used) / 3) if inv_obs else 0.5      # examined several epochs/bands
    notes = 1.0 if (payload.get("notes") or "").strip() else 0.0
    evidence = round(0.7 * base + 0.2 * breadth + 0.1 * notes, 3)

    if r.errors:
        status = "duplicate" if duplicate else "rejected"
    elif r.missing:
        status = "incomplete"
    elif r.warnings:
        status = "passed_with_warnings"
    else:
        status = "passed"
    return {"status": status, "checks": r.checks, "errors": r.errors, "missing": r.missing, "warnings": r.warnings,
            "evidence_score": evidence, "high_quality": evidence >= cfg["high_quality_evidence"] and status == "passed",
            "label": label, **r.facts,
            "disclaimer": "Automated quality checks only. This is not scientific validation."}


def _moving(inv, payload, inv_obs, r: Report, cfg) -> str | None:
    data = inv.get("data") or {}
    ms = payload.get("measurements") or []
    if not r.check("two_epochs", len(ms) >= 2, "measure the source in at least two observations", "missing"):
        return None
    pts = []
    for m in ms:
        try:
            ra, de = float(m["ra"]), float(m["dec"])
            unc = max(float(m.get("unc_arcsec") or 0), cfg["min_unc_arcsec"])
        except (KeyError, TypeError, ValueError):
            r.check("coordinates_valid", False, "each measurement needs numeric ra, dec (deg) and unc_arcsec")
            return None
        if not (0 <= ra < 360 and -90 <= de <= 90 and math.isfinite(unc)):
            r.check("coordinates_valid", False, f"position out of range ({ra}, {de})")
            return None
        o = inv_obs.get(str(m.get("obs_id")))
        pts.append({"ra": ra, "dec": de, "unc": unc, "t": _year(o.get("time")) if o else None, "obs": m.get("obs_id")})
    r.check("coordinates_valid", True, "all positions valid")

    far = [p for p in pts if _sep_arcsec(inv["ra"], inv["dec"], p["ra"], p["dec"]) > cfg["footprint_radius_deg"] * 3600]
    r.check("within_footprint", not far, f"{len(far)} positions are more than {cfg['footprint_radius_deg']}° from the target" if far
            else "all positions inside the investigation field")
    if far:
        return None
    epochs = {p["t"] for p in pts if p["t"] is not None}
    r.check("distinct_epochs", len(epochs) >= 2 or not inv_obs, "the measurements must come from different dates", "missing")

    pts.sort(key=lambda p: p["t"] or 0)
    a, b = pts[0], pts[-1]
    sep = _sep_arcsec(a["ra"], a["dec"], b["ra"], b["dec"])
    sig = math.hypot(a["unc"], b["unc"])
    z = sep / sig
    detected = z >= cfg["significance_sigma"]
    r.facts.update({"displacement_arcsec": round(sep, 3), "displacement_sigma": round(z, 2), "significant_motion": detected})
    claim = payload.get("moved")
    if claim is not None:
        r.check("claim_matches_measurement", bool(claim) == detected,
                f"you said {'moved' if claim else 'did not move'}, your positions give {z:.1f}σ", "warning")

    # Known motion: Gaia proper motion predicts the displacement between the two dates.
    pmra, pmdec = data.get("pmra_mas_yr"), data.get("pmdec_mas_yr")
    if pmra is not None and pmdec is not None and a["t"] is not None and b["t"] is not None and b["t"] > a["t"]:
        dt = b["t"] - a["t"]
        pe, pn = pmra * dt / 1000, pmdec * dt / 1000
        me, mn = _offset_arcsec(a["ra"], a["dec"], b["ra"], b["dec"])
        resid = math.hypot(me - pe, mn - pn)
        tol = max(cfg["significance_sigma"] * sig, PIXEL_ARCSEC)
        ok = resid <= tol
        r.facts.update({"predicted_displacement_arcsec": round(math.hypot(pe, pn), 3), "known_motion_residual_arcsec": round(resid, 3),
                        "known_motion_consistent": ok, "baseline_years": round(dt, 4)})
        r.check("known_motion_consistency", ok,
                f"measured motion differs from the Gaia proper motion by {resid:.1f}″ (tolerance {tol:.1f}″)" if not ok
                else f"matches Gaia proper motion to {resid:.1f}″", "warning")
        # Each position against the catalogue position propagated to that date.
        ep = 2016.0 if (data.get("position_epoch") or "").startswith("J2016") else 2000.0
        off = []
        for p in pts:
            if p["t"] is None:
                continue
            e, n = _offset_arcsec(inv["ra"], inv["dec"], p["ra"], p["dec"])
            off.append(math.hypot(e - pmra * (p["t"] - ep) / 1000, n - pmdec * (p["t"] - ep) / 1000) - cfg["significance_sigma"] * p["unc"])
        if off:
            worst = max(off)
            r.check("positional_consistency", worst <= PIXEL_ARCSEC * 2,
                    f"a position is {worst:.0f}″ beyond its uncertainty from the catalogue prediction" if worst > PIXEL_ARCSEC * 2
                    else "positions agree with the catalogue prediction", "warning")
    else:
        r.check("known_motion_consistency", None, "no catalogue proper motion or dates for comparison", "warning")

    if len(pts) >= 3 and all(p["t"] is not None for p in pts) and pts[-1]["t"] > pts[0]["t"]:
        t0, span = pts[0]["t"], pts[-1]["t"] - pts[0]["t"]
        e1, n1 = _offset_arcsec(a["ra"], a["dec"], b["ra"], b["dec"])
        worst = 0.0
        for p in pts[1:-1]:
            f = (p["t"] - t0) / span
            e, n = _offset_arcsec(a["ra"], a["dec"], p["ra"], p["dec"])
            worst = max(worst, math.hypot(e - f * e1, n - f * n1) / max(p["unc"], 1e-9))
        r.check("multi_epoch_consistency", worst <= cfg["significance_sigma"],
                f"intermediate positions depart {worst:.1f}σ from uniform motion" if worst > cfg["significance_sigma"]
                else "intermediate positions follow uniform motion", "warning")
    return "moved" if detected else "not_moved"
