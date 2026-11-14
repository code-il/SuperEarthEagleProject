"""Reproducible end-to-end check against the live archive for the 5 mandatory targets.

For each target this script:
  1. resolves the name with CDS Sesame (proper motion + parallax),
  2. searches DR1+QR3 for every SPHEREx image covering the position,
  3. lists observation dates/epochs and detector bands,
  4. downloads a few real FITS cutouts (D6 per epoch + one D1),
  5. checks that the brightest pixel in each D6 cutout lies near the
     proper-motion-predicted position (sanity check of times + WCS),
  6. looks one observation up by ID through IRSA TAP (cold index),
and writes example JSON to ``examples/``.

Usage (from the project root):
    python -m scripts.fetch_targets            # all five targets
    python -m scripts.fetch_targets wise-0855  # one target
"""
from __future__ import annotations

import json
import logging
import sys
import time
import warnings
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS

from backend.data_acquisition import (MANDATORY_TARGETS, archive, download_cutouts,
                                      get_available_bands, get_available_observation_dates,
                                      get_observation_metadata, get_settings, resolve_name,
                                      search_observations)
from backend.data_acquisition.archive import group_epochs
from backend.data_acquisition.fits_meta import quiet_wcs_logging

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples"
FOV_SEARCH = 0.05
FOV_CUTOUT = 0.03

log = logging.getLogger("fetch_targets")


def peak_offset_arcsec(path: str, expected: SkyCoord) -> float | None:
    """Distance from the expected position to the brightest pixel within 30 arcsec."""
    with fits.open(path) as h, quiet_wcs_logging(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        img = np.array(h["IMAGE"].data, dtype=float)
        w = WCS(h["IMAGE"].header)
        yy, xx = np.indices(img.shape)
        ra, dec = w.pixel_to_world_values(xx, yy)
        sep = SkyCoord(ra, dec, unit="deg").separation(expected).arcsec
        img[(sep > 30) | ~np.isfinite(img)] = -np.inf
        if not np.isfinite(img).any():
            return None
        iy, ix = np.unravel_index(np.argmax(img), img.shape)
        return float(sep[iy, ix])


def run_target(t: dict) -> dict:
    t0 = time.time()
    target = resolve_name(t["query"])
    ref = target.position_at(get_settings().reference_epoch)
    ra, dec = float(ref.ra.deg), float(ref.dec.deg)
    recs = search_observations(ra, dec, FOV_SEARCH)
    dates = get_available_observation_dates(ra, dec, FOV_SEARCH)
    bands = get_available_bands(ra, dec, FOV_SEARCH)
    epochs = group_epochs(recs)

    # pick: first D6 image of every epoch + the first D1 image
    by_id = {r.observation_id: r for r in recs}
    pick = []
    for e in epochs:
        d6 = [by_id[i] for i in e["observation_ids"]
              if by_id[i].detector == 6 and by_id[i].covers_position is not False]
        if d6:
            pick.append(d6[0])
    d1 = [r for r in recs if r.detector == 1 and r.covers_position is not False]
    if d1:
        pick.append(d1[0])
    batch = download_cutouts(pick, ra, dec, FOV_CUTOUT)

    checks = []
    for r in batch.records:
        if r.detector != 6:
            continue
        pred = target.position_at(r.observation_time)
        checks.append({
            "observation_id": r.observation_id, "time": r.observation_time,
            "wavelength_at_target_um": r.wavelength_at_target,
            "quality_flags": r.quality_flags,
            "peak_offset_from_predicted_arcsec": peak_offset_arcsec(r.local_path, pred),
            "peak_offset_from_J2000_arcsec": peak_offset_arcsec(r.local_path, target.skycoord()),
        })

    # cold lookup through TAP
    archive._index.clear()
    rec_file = get_settings().record_cache_dir / f"{recs[0].observation_id}.json"
    rec_file.unlink(missing_ok=True)
    tl = time.time()
    tap_rec = get_observation_metadata(recs[0].observation_id)
    tap_ok = tap_rec.fits_url == recs[0].fits_url and tap_rec.observation_time == recs[0].observation_time

    summary = {
        "target": t["label"], "resolved": target.to_dict(),
        "search_center_ra": ra, "search_center_dec": dec,
        "search_epoch": get_settings().reference_epoch,
        "n_observations": len(recs),
        "n_covering_position": sum(1 for r in recs if r.covers_position),
        "n_by_release": {k: sum(1 for r in recs if r.data_release == k)
                         for k in sorted({r.data_release for r in recs})},
        "first": dates["first"], "last": dates["last"],
        "epochs": [{k: v for k, v in e.items() if k != "observation_ids"} for e in epochs],
        "bands": bands,
        "cutouts": {"requested": len(pick), "ok": len(batch.records), "failed": batch.errors},
        "motion_checks_D6": checks,
        "tap_lookup_ok": tap_ok, "tap_lookup_seconds": round(time.time() - tl, 1),
        "elapsed_s": round(time.time() - t0, 1),
    }
    OUT.mkdir(exist_ok=True)
    (OUT / f"{t['key']}_search.json").write_text(json.dumps(
        {"query": {"ra": ra, "dec": dec, "fov": FOV_SEARCH}, "count": len(recs),
         "observations": [r.to_dict() for r in recs[:3]],
         "note": f"Truncated to 3 of {len(recs)} observations."}, indent=2, default=str))
    (OUT / f"{t['key']}_cutouts.json").write_text(json.dumps(
        {"observations": [r.to_dict() for r in batch.records]}, indent=2, default=str))
    return summary


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("backend.data_acquisition.http").setLevel(logging.WARNING)
    wanted = set(argv)
    results, failures = [], 0
    for t in MANDATORY_TARGETS:
        if wanted and t["key"] not in wanted:
            continue
        log.info("=== %s ===", t["label"])
        try:
            s = run_target(t)
            results.append(s)
            log.info("%s: %d images, %d epochs, cutouts %d/%d, TAP ok=%s, D6 peak offsets %s",
                     t["label"], s["n_observations"], len(s["epochs"]), s["cutouts"]["ok"],
                     s["cutouts"]["requested"], s["tap_lookup_ok"],
                     [c["peak_offset_from_predicted_arcsec"] and round(c["peak_offset_from_predicted_arcsec"], 1)
                      for c in s["motion_checks_D6"]])
        except Exception as exc:  # report and continue with the next target
            failures += 1
            log.exception("%s failed: %s", t["label"], exc)
            results.append({"target": t["label"], "error": str(exc)})
    OUT.mkdir(exist_ok=True)
    name = "live_summary.json" if not wanted else f"live_summary_{'_'.join(sorted(wanted))}.json"
    (OUT / name).write_text(json.dumps(results, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
