"""Validate the analysis engine on the five mandatory targets (live IRSA data).

For each target: resolve the name (CDS Sesame), process every D6 observation
of a 0.05 deg field through Person 2's ``process_series`` (which uses the data
acquisition module), run ``analyse_field`` with the target's ephemeris and
compare what was measured with the reference catalogues. Writes
``examples/analysis/<key>.json`` (full result), ``<key>_viewer.json`` (the
viewer contract) and ``validation_summary.json`` / ``.md``.

    python -m scripts.validate_analysis [target-key ...]
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

from backend.analysis import analyse_field, identify_moving_candidates, to_viewer
from backend.data_acquisition.coords import MANDATORY_TARGETS, resolve_name, resolve_target

OUT = Path(__file__).resolve().parents[1] / "examples" / "analysis"


def run(t: dict) -> dict:
    t0 = time.time()
    pos = resolve_target(t["query"])
    tgt = resolve_name(t["query"])
    res = analyse_field(pos["ra"], pos["dec"], 0.05, target=tgt, detectors=(6,), use_cache=False)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{t['key']}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    (OUT / f"{t['key']}_viewer.json").write_text(json.dumps(to_viewer(res), indent=1), encoding="utf-8")
    ti = res.get("target") or {}
    src = next((s for s in res["sources"] if s.get("is_target")), None)
    row = {"key": t["key"], "label": t["label"], "frames": res["summary"]["n_observations"],
           "visits": res["summary"]["n_visits"], "releases": res["provenance"]["releases"],
           "wavelength_source": res["provenance"]["wavelength_source"],
           "registration": res["registration_calibration"], "sources": res["summary"]["n_sources"],
           "classes": res["summary"]["classes"], "warnings": res["warnings"], "runtime_s": round(time.time() - t0, 1),
           "target_found": bool(ti.get("source_id")), "target_offset_median_arcsec": ti.get("median_offset_from_prediction_arcsec"),
           "catalogue_pm": ti.get("catalogue_pm_mas_yr")}
    if src:
        m = src.get("angular_motion") or {}
        km = src.get("known_catalogue_match") or {}
        row.update(measured_pm=[m.get("pm_ra_cosdec_mas_yr"), m.get("pm_dec_mas_yr")],
                   measured_pm_err=[m.get("pm_ra_cosdec_err_mas_yr"), m.get("pm_dec_err_mas_yr")],
                   motion_sigma=m.get("motion_sigma"), linear_chi2_red=m.get("linear_chi2_red"),
                   parallax_removed_mas=m.get("parallax_removed_mas"),
                   classification=src["classification"], label_text=src["label"], confidence=src["confidence"],
                   status=src["status"], subtype=src.get("subtype"), saturated=src["saturated"],
                   catalogue_match=km.get("id"), catalogue=km.get("catalog"), match_sep_arcsec=km.get("separation_arcsec"),
                   pm_difference_sigma=km.get("pm_difference_sigma"),
                   variability=(src.get("variability_indicator") or {}).get("sigma"),
                   variability_amp_mag=(src.get("variability_indicator") or {}).get("amplitude_mag"),
                   variability_reliable=(src.get("variability_indicator") or {}).get("reliable"),
                   light_curve_points=len((src.get("light_curve") or {}).get("points", [])))
        cat = ti.get("catalogue_pm_mas_yr") or [None, None]
        if cat[0] is not None and m.get("pm_ra_cosdec_mas_yr") is not None:
            d = math.hypot(m["pm_ra_cosdec_mas_yr"] - cat[0], m["pm_dec_mas_yr"] - cat[1])
            row["pm_vs_ephemeris_sigma"] = round(d / max(m.get("pm_err_mas_yr") or 1, 1), 2)
    row["candidates_not_known"] = [
        {"id": s["source_id"], "class": s["classification"], "confidence": s["confidence"], "note": s["note"]}
        for s in res["sources"] if s["status"] == "candidate" and s["classification"] not in ("stationary", "insufficient_data")]
    row["moving"] = [s["source_id"] for s in identify_moving_candidates(res["sources"])]
    return row


def _fmt(v, nd=2):
    return "–" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def main(argv: list[str]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    targets = [t for t in MANDATORY_TARGETS if not argv or t["key"] in argv]
    rows = []
    for t in targets:
        print(f"== {t['label']}", flush=True)
        try:
            rows.append(run(t))
        except Exception as exc:  # keep going; report the failure
            rows.append({"key": t["key"], "label": t["label"], "error": f"{type(exc).__name__}: {exc}"})
        print(json.dumps(rows[-1], default=str)[:600], flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "validation_summary.json").write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    lines = ["| Target | Frames / visits | Target vs ephemeris (median) | Measured µ (mas/yr) | Catalogue µ | Δµ (σ) | Motion σ | Class (confidence) | Catalogue match |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['label']} | error: {r['error'][:60]} |||||||")
            continue
        pm = r.get("measured_pm") or [None, None]
        pe = r.get("measured_pm_err") or [None, None]
        cp = r.get("catalogue_pm") or [None, None]
        mpm = "–" if pm[0] is None else f"{pm[0]:.0f} ± {pe[0]:.0f}, {pm[1]:.0f} ± {pe[1]:.0f}"
        cpm = "–" if cp[0] is None else f"{cp[0]:.0f}, {cp[1]:.0f}"
        lines.append(f"| {r['label']} | {r['frames']} / {r['visits']} | {_fmt(r.get('target_offset_median_arcsec'))}\" | {mpm} | {cpm} | "
                     f"{_fmt(r.get('pm_vs_ephemeris_sigma'))} | {_fmt(r.get('motion_sigma'), 1)} | "
                     f"{r.get('label_text', 'not found')} ({_fmt(r.get('confidence'))}) | {r.get('catalogue_match') or '–'} |")
    (OUT / "validation_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1:])
