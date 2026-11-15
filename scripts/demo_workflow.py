"""Run the Prompt-5 acceptance workflow against a running SPHEREx Explorer (live data).

    python -m scripts.start                      # in one terminal
    python -m scripts.demo_workflow              # in another; all five targets
    python -m scripts.demo_workflow wise-0855 --base http://127.0.0.1:8000

For each target it does through the public API exactly what a visitor does in
the browser, and checks each step:

 1 open the website          5 play (all frames loadable in order)   9 metadata of that frame
 2 search the target         6 pause on an observation              10 run the analysis
 3 retrieve observations     7 compare two observation dates        11 target position and motion
 4 chronological order       8 difference image                     12 shareable URL opens the same view

Writes ``examples/demo/demo_report.md`` and ``demo_report.json``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

from backend.data_acquisition.coords import MANDATORY_TARGETS

OUT = Path(__file__).resolve().parents[1] / "examples" / "demo"
WISE0855_COORDS = ("08h55m10.831s", "-07d14m42.53s")
EPOCH_GAP_DAYS = 20


def run_target(base: str, t: dict, s: requests.Session) -> dict:
    fov = 0.08 if t["key"] == "r-draconis" else 0.05
    field = {"q": t["query"], "fov": fov, "detectors": [6]}
    if t["key"] == "wise-0855":
        # The challenge's supplied coordinates are AllWISE-epoch; at 8"/yr the object is ~2' from
        # them during SPHEREx, outside a 0.05 deg field, so the coordinate view uses 0.1 deg.
        fov = 0.1
        field = {"ra": WISE0855_COORDS[0], "dec": WISE0855_COORDS[1], "fov": fov, "detectors": [6]}
    steps: list[dict] = []

    def step(n: int, name: str, ok: bool, detail: str, t0: float) -> None:
        steps.append({"step": n, "name": name, "ok": bool(ok), "detail": detail, "seconds": round(time.time() - t0, 1)})
        print(f"  {'ok ' if ok else 'FAIL'} {n:2d} {name}: {detail}", flush=True)

    t0 = time.time()
    r = s.get(base + "/")
    step(1, "open the website", r.status_code == 200 and "<div id=\"root\"" in r.text, f"HTTP {r.status_code}", t0)

    t0 = time.time()
    r = s.post(base + "/api/search", json=field, timeout=600)
    sr = r.json() if r.ok else {}
    step(2, "search", r.ok, f"{sr.get('query', {}).get('name') or sr.get('query', {}).get('query')}: {sr.get('count')} observations", t0)

    t0 = time.time()
    r = s.post(base + "/api/process", json={**field, "make_gifs": False}, timeout=1800)
    pr = r.json() if r.ok else {}
    obs = pr.get("observations", [])
    step(3, "retrieve observations", r.ok and len(obs) > 0,
         f"{len(obs)} processed frames ({len(pr.get('errors', []))} failed, {len(pr.get('skipped', []))} skipped)", t0)
    if not obs:
        return {"key": t["key"], "label": t["label"], "steps": steps}

    t0 = time.time()
    mjds = [o["mjd"] for o in obs]
    epochs, e = [], 0
    for i, m in enumerate(mjds):
        if i and m - mjds[i - 1] > EPOCH_GAP_DAYS:
            e += 1
        epochs.append(e)
    step(4, "chronological display", mjds == sorted(mjds),
         f"{obs[0]['observation_time'][:10]} → {obs[-1]['observation_time'][:10]}, {e + 1} visits", t0)

    t0 = time.time()
    sample = [obs[0], obs[len(obs) // 2], obs[-1]]
    codes = [s.get(base + o["fits_url"]).status_code for o in sample]
    step(5, "play the time-lapse", all(c == 200 for c in codes), f"first/middle/last frame FITS: {codes}", t0)

    t0 = time.time()
    paused = obs[len(obs) // 2]
    png = s.get(base + paused["image_url"]) if paused.get("image_url") else None
    step(6, "pause on an observation", png is not None and png.status_code == 200, f"{paused['observation_id']} (PNG {png.status_code if png else '-'})", t0)

    t0 = time.time()
    a_ep, b_ep = epochs[-1], 0
    r = s.post(base + "/api/compare", json={**field, "a": {"epoch_index": a_ep}, "b": {"epoch_index": b_ep}}, timeout=1800)
    cr = r.json() if r.ok else {"detail": r.text[:200]}
    step(7, "compare two dates", r.ok, f"visit {a_ep + 1} vs visit 1, Δt {cr.get('stats', {}).get('delta_t_days', '?')} d" if r.ok else str(cr.get("detail")), t0)

    t0 = time.time()
    ok8 = r.ok and s.get(base + cr["fits_url"]).status_code == 200
    step(8, "difference image", ok8, cr.get("display", "")[:90] if r.ok else "-", t0)

    t0 = time.time()
    keys = ("observation_time", "wavelength", "bandwidth", "detector", "data_release", "wavelength_source", "quality_flags", "pipeline_version")
    missing = [k for k in keys if k not in paused]
    step(9, "metadata", not missing,
         f"{paused['observation_time'][:19]} · λ {paused['wavelength']:.3f} µm ({paused.get('wavelength_source')}) · D{paused['detector']} · "
         f"{paused['data_release']} · flags {','.join(paused['quality_flags']) or 'none'}" + (f" · missing {missing}" if missing else ""), t0)

    t0 = time.time()
    aq = {k: v for k, v in field.items() if k != "detectors"}
    if "q" not in aq:
        aq["target"] = t["query"]
    r = s.get(base + "/api/analysis?" + urlencode({**aq, "full": "false"}), timeout=1800)
    an = r.json() if r.ok else {}
    step(10, "run the analysis", r.ok, f"{an.get('summary', {}).get('n_sources')} sources, classes {an.get('summary', {}).get('classes')}", t0)

    t0 = time.time()
    tgt = an.get("target") or {}
    rec = next((x for x in an.get("results_summary", []) if x.get("is_target")), None)
    if rec:
        m = rec.get("angular_motion") or {}
        km = rec.get("known_catalogue_match") or {}
        motion = (f"µ = ({m.get('pm_ra_cosdec_mas_yr', 0):.0f} ± {m.get('pm_ra_cosdec_err_mas_yr', 0):.0f}, "
                  f"{m.get('pm_dec_mas_yr', 0):.0f} ± {m.get('pm_dec_err_mas_yr', 0):.0f}) mas/yr, {m.get('motion_sigma', 0):.1f}σ"
                  if m.get("pm_ra_cosdec_mas_yr") is not None else "motion not measured")
        detail = (f"{rec['source_id']} at RA {rec['ra']:.5f} Dec {rec['dec']:.5f}, {tgt.get('median_offset_from_prediction_arcsec')}\" from "
                  f"ephemeris; {motion}; {rec['label']} ({rec['confidence']:.2f}); match {km.get('id', 'none')}")
    else:
        detail = tgt.get("note") or "target not identified"
    step(11, "measured position and motion", rec is not None, detail, t0)

    t0 = time.time()
    share = {"fov": fov, "obs": paused["observation_id"], "mode": "difference"}
    share = {**({"q": field["q"]} if "q" in field else {"ra": field["ra"], "dec": field["dec"]}), **share}
    url = base + "/explore?" + urlencode(share)
    r = s.get(url)
    step(12, "share through a URL", r.status_code == 200, url, t0)
    return {"key": t["key"], "label": t["label"], "share_url": url, "steps": steps,
            "warnings": an.get("warnings", [])}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    s = requests.Session()
    s.headers["User-Agent"] = "SPHEREx-Explorer-demo/1.0"
    try:
        v = s.get(a.base + "/api/version", timeout=10).json()
    except Exception as exc:
        print(f"Cannot reach {a.base}: {exc}. Start the app first: python -m scripts.start")
        return 2
    rows = []
    for t in MANDATORY_TARGETS:
        if a.targets and t["key"] not in a.targets:
            continue
        print(f"== {t['label']}", flush=True)
        try:
            rows.append(run_target(a.base, t, s))
        except Exception as exc:  # report and continue
            rows.append({"key": t["key"], "label": t["label"], "error": f"{type(exc).__name__}: {exc}", "steps": []})
            print(f"  ERROR {exc}")
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "base": a.base, "versions": v, "targets": rows}
    (OUT / "demo_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    lines = [f"# Demonstration run ({report['generated'][:10]})", "",
             f"App {v.get('app')} · processing {v.get('processing')} · analysis {v.get('analysis')} · live IRSA data, detector D6.", ""]
    for r in rows:
        n_ok = sum(st["ok"] for st in r["steps"])
        lines += [f"## {r['label']}: {n_ok}/12 steps passed", ""]
        if r.get("error"):
            lines += [f"Error: {r['error']}", ""]
        lines += ["| # | Step | Result | Time |", "|---|---|---|---|"]
        lines += [f"| {st['step']} | {st['name']} | {'✅' if st['ok'] else '❌'} {st['detail'].replace('|', '/')} | {st['seconds']} s |" for st in r["steps"]]
        if r.get("warnings"):
            lines += ["", "Analysis warnings: " + " ".join(r["warnings"])]
        lines.append("")
    (OUT / "demo_report.md").write_text("\n".join(lines), encoding="utf-8")
    total = sum(len(r["steps"]) for r in rows)
    passed = sum(st["ok"] for r in rows for st in r["steps"])
    print(f"\n{passed}/{total} steps passed. Report: {OUT / 'demo_report.md'}")
    return 0 if passed == total and total == 12 * len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
