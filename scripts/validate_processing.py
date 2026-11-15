"""Scientific validation of the image-processing engine on the 5 mandatory targets.

For each target (detector D6, 0.05 deg field) it:
  1. processes every observation onto the common north-up grid,
  2. measures the target's centroid in each aligned frame against its
     proper-motion-predicted position (alignment + timing + coordinates),
  3. compares the target's sky position and aperture flux in the native
     frame vs the aligned frame (reprojection must not shift or lose flux),
  4. tracks a field star (if one exists) across frames (alignment precision),
  5. builds first-vs-last epoch difference images (wavelength-matched),
  6. writes before/after figures and a JSON summary to examples/processing/.

Usage:  python -m scripts.validate_processing [target-key ...]
"""
from __future__ import annotations

import json
import logging
import sys
import warnings
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from backend.data_acquisition import MANDATORY_TARGETS, get_settings, resolve_name
from backend.data_acquisition.fits_meta import quiet_wcs_logging
from backend.image_processing import (compare, generate_display_image, load_spherex_fits, make_stacks,
                                      process_series, select_observations)
from backend.image_processing.align import centroid
from backend.image_processing.display import compute_limits, upscale
from backend.image_processing.pipeline import assign_epochs, processed_root

OUT = Path(__file__).resolve().parents[1] / "examples" / "processing"
FOV = 0.05
log = logging.getLogger("validate")


def aperture_flux(img: np.ndarray, x: float, y: float, r: float = 3.0, r_in: float = 5.0, r_out: float = 8.0):
    yy, xx = np.indices(img.shape)
    d = np.hypot(xx - x, yy - y)
    ap = d <= r
    ann = (d >= r_in) & (d <= r_out) & np.isfinite(img)
    if not np.isfinite(img[ap]).all() or ann.sum() < 5:
        return None
    bkg = np.median(img[ann])
    return float((img[ap] - bkg).sum())


def native_measure(path: str, ra: float, dec: float):
    """Centroid (as sky position) and aperture flux of a source in the native cutout."""
    im = load_spherex_fits(path)
    data = im.image - (im.zodi if im.zodi is not None else 0)
    with quiet_wcs_logging(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        x, y = im.wcs.world_to_pixel_values(ra, dec)
        c = centroid(data - np.nanmedian(data), float(x), float(y), 2.5)
        if c is None:
            return None
        if not np.isfinite(data[int(round(c[1])) - 2:int(round(c[1])) + 3,
                                int(round(c[0])) - 2:int(round(c[0])) + 3]).all():
            return None
        sky = im.wcs.pixel_to_world_values(*c)
    return {"ra": float(sky[0]), "dec": float(sky[1]), "flux": aperture_flux(data, *c),
            "rgba": generate_display_image(data, stretch="asinh")}


def sep_arcsec(ra1, dec1, ra2, dec2) -> float:
    from astropy.coordinates import SkyCoord

    return float(SkyCoord(ra1, dec1, unit="deg").separation(SkyCoord(ra2, dec2, unit="deg")).arcsec)


def label(img: Image.Image, text: str) -> Image.Image:
    canvas = Image.new("RGB", (img.width, img.height + 14), (20, 20, 20))
    canvas.paste(img, (0, 14))
    ImageDraw.Draw(canvas).text((2, 1), text, fill=(230, 230, 230))
    return canvas


def to_rgb(rgba: np.ndarray, size: int) -> Image.Image:
    factor = max(1, size // max(rgba.shape[:2]))
    big = upscale(rgba, factor)
    base = Image.new("RGB", (big.shape[1], big.shape[0]), (40, 0, 40))  # purple = no data
    base.paste(Image.fromarray(big, "RGBA"), mask=Image.fromarray(big[..., 3]))
    return base.resize((size, size), Image.NEAREST)


def run(t: dict) -> dict:
    target = resolve_name(t["query"])
    ref = target.position_at(get_settings().reference_epoch)
    ra0, dec0 = float(ref.ra.deg), float(ref.dec.deg)
    series = process_series(ra0, dec0, FOV, detectors=[6], make_gifs=True)
    obs = series.observations
    wcs = obs[0].wcs

    # 2/3: target centroid in aligned frames vs prediction; native vs aligned
    rows = []
    for o in obs:
        pred = target.position_at(o.observation_time)
        with quiet_wcs_logging():
            px, py = wcs.world_to_pixel_values(pred.ra.deg, pred.dec.deg)
        bg_raw = o.raw - np.nanmedian(o.raw[o.mask == 0]) if (o.mask == 0).any() else o.raw
        c = centroid(bg_raw, float(px), float(py), 2.5)
        row = {"observation_id": o.observation_id, "time": o.observation_time, "wavelength": o.wavelength,
               "quality_flags": o.quality_flags}
        if c is not None:
            with quiet_wcs_logging():
                sra, sdec = wcs.pixel_to_world_values(*c)
            row["aligned_offset_from_predicted_arcsec"] = sep_arcsec(sra, sdec, pred.ra.deg, pred.dec.deg)
            src = next((p for p in [o.source_path] if p), None)
            from backend.data_acquisition.archive import _load_record

            rec = _load_record(o.observation_id)
            nat = native_measure(rec.local_path, pred.ra.deg, pred.dec.deg) if rec and rec.local_path else None
            if nat:
                row["native_vs_aligned_position_arcsec"] = sep_arcsec(nat["ra"], nat["dec"], sra, sdec)
                fa = aperture_flux(bg_raw, *c)
                if nat["flux"] and fa and nat["flux"] > 0:
                    row["aligned_over_native_flux"] = fa / nat["flux"]
        rows.append(row)

    def stat(key):
        v = [r[key] for r in rows if r.get(key) is not None]
        return {"n": len(v), "median": float(np.median(v)) if v else None,
                "p90": float(np.percentile(v, 90)) if v else None}

    # 4: brightest stationary source in the deep stack, away from the target's track
    stacks = make_stacks(obs, "detector", "median", output_dir=processed_root() / "validation_stacks")
    deep = np.nanmedian(np.stack([o.data for o in obs]), axis=0)
    track = [wcs.world_to_pixel_values(*(lambda c: (c.ra.deg, c.dec.deg))(target.position_at(o.observation_time)))
             for o in obs]
    yy, xx = np.indices(deep.shape)
    far = np.ones(deep.shape, bool)
    for x, y in track:
        far &= np.hypot(xx - x, yy - y) > 5
    far &= (xx > 3) & (yy > 3) & (xx < deep.shape[1] - 4) & (yy < deep.shape[0] - 4)
    field_star = None
    cand = np.where(far & np.isfinite(deep), deep, -np.inf)
    if np.isfinite(cand).any():
        iy, ix = np.unravel_index(np.argmax(cand), cand.shape)
        noise = np.nanstd(deep[far])
        if deep[iy, ix] > 10 * noise:
            ref_c = centroid(deep, ix, iy, 2.5)
            offs = []
            for o in obs:
                c = centroid(o.data, ref_c[0], ref_c[1], 2.5)
                if c is not None:
                    offs.append(float(np.hypot(c[0] - ref_c[0], c[1] - ref_c[1]) * 6.15))
            field_star = {"pixel": [float(ref_c[0]), float(ref_c[1])], "peak_snr": float(deep[iy, ix] / noise),
                          "offset_from_mean_arcsec_median": float(np.median(offs)) if offs else None,
                          "offset_from_mean_arcsec_p90": float(np.percentile(offs, 90)) if offs else None,
                          "n_frames": len(offs)}

    # 5: first vs last epoch, wavelength matched if possible
    ep = assign_epochs(obs)
    first, last = min(ep.values()), max(ep.values())
    A = select_observations(obs, epoch_index=last)
    B = select_observations(obs, epoch_index=first)
    try:
        cmp_ = compare(A, B, "difference", match_wavelength=True, name=f"val_{t['key']}_diff")
        matched = True
    except ValueError:
        cmp_ = compare(A, B, "difference", match_wavelength=False, name=f"val_{t['key']}_diff")
        matched = False

    # 6: before/after figure: one frame per epoch, native (top) vs aligned (bottom)
    lim = series.display_limits["D6"]
    tiles_top, tiles_bot = [], []
    from backend.data_acquisition.archive import _load_record

    for e in sorted(set(ep.values())):
        o = select_observations(obs, epoch_index=e)[0]
        rec = _load_record(o.observation_id)
        nat = load_spherex_fits(rec.local_path)
        nat_rgba = generate_display_image(nat.image - (nat.zodi if nat.zodi is not None else 0), stretch="asinh")
        tiles_top.append(label(to_rgb(nat_rgba, 160), f"native {o.observation_time[:10]}"))
        tiles_bot.append(label(to_rgb(generate_display_image(o.data, stretch="asinh", vmin=lim[0], vmax=lim[1]), 160),
                               f"aligned {o.wavelength:.2f}um"))
    w = sum(t_.width + 4 for t_ in tiles_top)
    fig = Image.new("RGB", (w, tiles_top[0].height * 2 + 4), (0, 0, 0))
    x = 0
    for a, b in zip(tiles_top, tiles_bot):
        fig.paste(a, (x, 0))
        fig.paste(b, (x, a.height + 4))
        x += a.width + 4
    OUT.mkdir(parents=True, exist_ok=True)
    fig.save(OUT / f"{t['key']}_before_after.png")
    diff_root = processed_root() / "compare"
    for suffix in ("", "_sig"):
        src = diff_root / f"val_{t['key']}_diff{suffix}.png"
        if src.exists():
            im = Image.open(src).convert("RGBA")
            im.resize((im.width * 8, im.height * 8), Image.NEAREST).save(OUT / f"{t['key']}_diff{suffix}.png")
    if series.animations.get("D6"):
        Image.open(series.animations["D6"]).save(OUT / f"{t['key']}_timelapse_D6.gif", save_all=True)

    return {
        "target": t["label"], "n_frames": len(obs), "n_epochs": len(set(ep.values())),
        "grid": {k: series.grid[k] for k in ("shape", "pixel_scale_arcsec", "orientation")},
        "errors": series.errors, "skipped": len(series.skipped),
        "target_offset_from_predicted_arcsec": stat("aligned_offset_from_predicted_arcsec"),
        "native_vs_aligned_position_arcsec": stat("native_vs_aligned_position_arcsec"),
        "aligned_over_native_flux": stat("aligned_over_native_flux"),
        "field_star": field_star,
        "first_vs_last_epoch": {"wavelength_matched": matched, "stats": cmp_["stats"],
                                "a_wavelength": cmp_["a"]["wavelength_mean"],
                                "b_wavelength": cmp_["b"]["wavelength_mean"], "caveats": cmp_["caveats"]},
        "detector_stack_warnings": stacks[0]["warnings"] if stacks else None,
        "frames": rows,
    }


def main(argv):
    logging.basicConfig(level=logging.WARNING)
    out = []
    for t in MANDATORY_TARGETS:
        if argv and t["key"] not in argv:
            continue
        try:
            s = run(t)
        except Exception as exc:
            log.exception("failed %s", t["label"])
            s = {"target": t["label"], "error": str(exc)}
        out.append(s)
        if "error" not in s:
            print(f"{s['target']}: {s['n_frames']} frames/{s['n_epochs']} epochs | target vs predicted "
                  f"median {s['target_offset_from_predicted_arcsec']['median']:.2f}\" | native vs aligned "
                  f"{s['native_vs_aligned_position_arcsec']['median']}\" | flux ratio "
                  f"{s['aligned_over_native_flux']['median']} | field star {s['field_star'] and s['field_star']['offset_from_mean_arcsec_median']} | "
                  f"diff sig std {s['first_vs_last_epoch']['stats'].get('significance_robust_std')}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "validation_summary.json").write_text(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
