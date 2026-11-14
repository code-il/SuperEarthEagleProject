# SPHEREx image processing engine (Workstream 2)

Turns SPHEREx Level-2 FITS (from the [data acquisition engine](../data_acquisition/README.md))
into cleaned, aligned images on one common sky grid. It also builds the
stacks, difference and ratio images and display PNG/GIFs that the viewer
(Workstream 3) shows and the analysis engine (Workstream 4) measures.

No archive queries of its own (it calls Workstream 1), no web UI, no
moving-object detection.

## Contents
- [What the data looks like](#what-the-data-looks-like-and-why-it-matters)
- [Processing steps](#processing-steps)
- [Python interface](#python-interface)
- [HTTP API](#http-api)
- [Output products](#output-products)
- [Validation on the 5 mandatory targets](#validation-on-the-5-mandatory-targets)
- [Testing](#testing)
- [Known limitations and scientific caveats](#known-limitations-and-scientific-caveats)

## What the data looks like (and why it matters)

Measured from 26 real DR1/QR3 cutouts of the five targets:

| Property | Finding | Consequence |
|---|---|---|
| Extensions | `IMAGE` (MJy/sr), `FLAGS`, `VARIANCE`, `ZODI`, `EPSF`, `WCS-WAVE`; primary HDU empty | Everything is read from the extensions, nothing from the primary |
| Orientation | Same target, different epochs: rotated −161°, +22°, +152°, … | Frames must be resampled onto one grid before comparison |
| Background | `ZODI` model is most of the sky in D6 (0.27–0.92 MJy/sr); a residual of ~0.03–0.06 MJy/sr remains | Subtract ZODI, then a sigma-clipped median |
| Wavelength across a cutout | Only ~0.005 µm (vs ~0.03 µm channel width) | One cutout ≈ one spectral channel |
| Wavelength between exposures | D6 exposures of one target range 4.41–5.00 µm | "Same detector" ≠ "same wavelength"; comparisons need wavelength matching |
| Saturated targets | Barnard's Star, R Dra, RW Aur: NaN cores, `OVERFLOW`/`SUR_ERROR`/`BLOOM` | Masked by default; photometry at the core is not possible |
| Flags | `SOURCE` on 76% of pixels (informational); `TRANSIENT` 7%, `BLOOM` 6%, `SUR_ERROR` 5% (mostly around bright stars) | Mask profiles (below) |
| PSF | FWHM 7.5″ (D1), 9.0″ (D6) on 6.15″ pixels | Undersampled: see caveats |

## Processing steps

1. **Read** (`load_spherex_fits`): science, variance, flags (with bit names decoded from the file's own header), zodi model, celestial WCS with SIP distortion, and a per-pixel wavelength map from the spectral WCS.
2. **Prepare** (`prepare_image`):
   - non-finite pixels and bad variances are marked invalid;
   - quality flags are masked according to a profile;
   - background is removed (`zodi+median` by default);
   - the original values are kept separately (`RAW`).

   | Mask profile | Masks |
   |---|---|
   | `none` | nothing |
   | `minimal` | NONFUNC, MISSING_DATA, HOT, COLD |
   | `standard` (default) | + SUR_ERROR, OVERFLOW, NONLINEAR, PERSIST, OUTLIER |
   | `strict` | every non-informational flag (adds BLOOM, HALO, GHOST*, CROSSTALK, STREAK, SNOWBALL, TRANSIENT, …) |

   `SOURCE` and `FULLSAMPLE` are informational and never masked. A list of flag names can be given instead of a profile.
3. **Align** (`make_reference_wcs`, `reproject_observation`): every frame is resampled with its own WCS onto a north-up, east-left TAN grid. The grid is centred exactly on the requested position (odd pixel count) at the native 6.15″/px, or a chosen scale. Methods are `interp` (bilinear, default), `exact` (flux-conserving) and `nearest`; flags use nearest-neighbour, so bits are never blended. Cutouts are requested √2× larger than the field, so any rotation still covers the grid. Footprint masks mark areas with no coverage.
4. **Combine** (`stack_observations`): `mean`, `median` or inverse-variance `weighted`, NaN-aware, with propagated variance and a per-pixel frame count. Stacks are grouped by `epoch`, `date`, `detector`, `channel` or `epoch_channel`. Detectors are never mixed unless explicitly allowed, and stacks that span more than one spectral channel carry a warning.
5. **Compare** (`subtract_images`, `divide_images`, `compare`):
   - **difference A − B:** background offset removal, Gaussian PSF matching to the broader FWHM, an optional common smoothing, and a significance map (Δ/σ);
   - **ratio A / B:** computed only where B has S/N ≥ 5, so it is defined on sources and not on noise;
   - **wavelength matching:** `match_wavelength=True` keeps only frame pairs observed within one channel of each other.

   Every comparison reports its statistics and auto-generated caveats.
6. **Render** (`generate_display_image`): asinh, linear, sqrt or log greyscale. Diverging blue-white-red is used for differences (zero is exactly white). Invalid pixels are transparent. One set of display limits is shared per detector across a whole time series, so the time-lapse doesn't flicker. GIF time-lapses are made per detector.

## Python interface

```python
from backend.image_processing import (process_series, make_stacks, compare,
                                      select_observations)

series = process_series(269.4462, 4.7688, fov=0.05, detectors=[6])   # fetch + align + render
stacks = make_stacks(series.observations, group_by="epoch", method="median")
A = select_observations(series.observations, epoch_index=3)
B = select_observations(series.observations, epoch_index=0)
diff = compare(A, B, "difference", match_wavelength=True)
print(diff["image_url"], diff["stats"], diff["caveats"])
```

Lower-level functions, as listed in the brief:

| Function | Purpose |
|---|---|
| `load_spherex_fits(path)` / `extract_science_image(hdul)` | Read one file into a `SpherexImage` |
| `prepare_image(image, uncertainty, quality, ...)` | Masks + background; returns `PreparedImage` (data, variance, mask, original, history) |
| `make_reference_wcs(ra, dec, fov)` / `select_reference(...)` | Common grid / reference choice |
| `reproject_observation(image, input_wcs, reference_wcs, shape)` | Resample one array; returns (array, footprint) |
| `stack_observations(images, method, variances)` | Co-add |
| `subtract_images(a, b, ...)` / `divide_images(a, b, ...)` | Difference / ratio with variance and significance |
| `generate_display_image(image, stretch, ...)` | RGBA rendering |
| `save_processed_observation(image, metadata, output_path)` | Write the processed FITS |
| `process_observation(...)`, `process_series(...)`, `make_stacks(...)`, `compare(...)` | Higher-level pipeline |

## HTTP API

| Method & path | Description |
|---|---|
| `GET /api/processing/options` | Valid mask profiles, background methods, stretches, stack methods, groupings |
| `GET /api/channels` | The 102 nominal channels (17 per detector, log-spaced) |
| `POST /api/process` | Search + cutouts + align + render; optional `stack_by`. Returns per-observation products, stacks and GIF URLs |
| `POST /api/compare` | `a` and `b` selectors (`observation_ids`, `epoch_index`, `date_start/end`, `wavelength_min/max`); `mode` = `difference` or `ratio`; `match_wavelength`, `psf_matching`, `smooth_fwhm_arcsec` |
| `GET /api/files/processed/{path}` | Serve processed FITS / PNG / GIF |

Both POST routes accept the search fields of `/api/search` (`q` or `ra`/`dec`,
`fov`, `detectors`, dates, `release`) plus `pixel_scale_arcsec`, `mask_profile`,
`background`, `reproject_method`, `stretch` and `max_observations` (default 120).
Restrict `detectors`: every detector adds ~60 downloads of ~4 MB.

```bash
curl -X POST http://localhost:8000/api/compare -H "Content-Type: application/json" -d "{\"q\": \"Barnard's Star\", \"fov\": 0.05, \"detectors\": [6], \"a\": {\"epoch_index\": 3}, \"b\": {\"epoch_index\": 0}, \"match_wavelength\": true}"
```

## Output products

**Per observation** (integration contract + extras):

```json
{
  "observation_id": "2025W33_2C_0321_1_D6",
  "observation_time": "2025-08-15T20:45:11.376Z",
  "wavelength": 4.9233,
  "image_url": "/api/files/processed/<series>/2025W33_2C_0321_1_D6.png",
  "fits_url": "/api/files/processed/<series>/2025W33_2C_0321_1_D6.fits",
  "wcs": {"CTYPE1": "RA---TAN", "CRVAL1": 269.4462, "CDELT1": -0.001708, "...": "..."},
  "quality_flags": ["BLOOM", "OVERFLOW", "SUR_ERROR", "TRANSIENT"],
  "processing_method": "mask=standard; background=zodi+median; reproject=interp @ 6.15\"/px; v1.1",
  "detector": 6,
  "channel": {"index": 99, "label": "D6-14 (4.917 um)"},
  "valid_fraction": 0.9677,
  "background_level": 0.1089,
  "display_limits": [-0.06, 41.2],
  "history": ["mask profile 'standard' (...): 17 pixels masked", "subtracted SPHEREx ZODI model (median 0.3741 MJy/sr)", "..."]
}
```

**Processed FITS** (`<observation_id>.fits`): every image extension carries the common grid's WCS.

| Extension | Content |
|---|---|
| PRIMARY | Provenance: OBS_ID, DATE-OBS, MJD-OBS, WAVELEN, CHANNEL, PSF_FWHM, MASKPROF, BKGMETH, BKGLEVEL, ZODIMED, REPROJ, PIXSCALE, SRCSHA, HISTORY |
| SCI | Background-subtracted MJy/sr; NaN where invalid |
| VAR | Variance |
| MASK | uint8: 1 NO_COVERAGE, 2 FLAGGED, 4 NONFINITE, 8 BAD_VARIANCE, 16 INTERP_EDGE (documented in `MASKB*` cards) |
| FLAGS | Original SPHEREx bits (definitions in `MSKN/MSKM/MSKD` cards) |
| RAW | Reprojected original values: no background subtraction, no masking |
| WAVE | Wavelength (µm) of every pixel in this exposure |

**Stacks**: `SCI`, `VAR` and `NFRAMES`. **Comparisons**: `DIFF` or `RATIO`, `VAR`, `SIG` and `MASK`, plus `<name>.png` and `<name>_sig.png`.

Products are cached under `cache/processed/<series-hash>/`. A frame is
re-processed only when its source cutout (sha256) or the processing
parameters change.

## Validation on the 5 mandatory targets

`python -m scripts.validate_processing` processes every D6 observation of
each target (0.05° field) and writes `examples/processing/`:
- `validation_summary.json`
- `<target>_before_after.png` (native frames on top, aligned below)
- `<target>_diff.png` / `_diff_sig.png`
- `<target>_timelapse_D6.gif`

| Target | Frames / epochs | Target vs predicted (median, p90) | Native vs aligned position (median) | Aligned / native flux (median) | First-vs-last epoch: Δt, robust σ of significance, pixels \|S\|>5 |
|---|---|---|---|---|---|
| Barnard's Star | 58 / 4 | 0.68" , 1.15" | 0.10" | 0.996 | 362 d, 1.43, 149 |
| Epsilon Indi B | 93 / 6 | 0.99" , 1.29" | 0.14" | 0.996 | 512 d, 0.70, 18 |
| WISE 0855 | 45 / 4 | 1.89" , 3.70" | 0.81" | 0.925 | 365 d, 0.55, 2 |
| RW Aurigae | 56 / 3 | 0.49" , 1.15" | 0.05" | 0.999 | 362 d, 1.45, 135 |
| R Draconis | 91 / 3 | 2.63" , 4.60" | n/a | n/a | 347 d, 26.06, 736 |

Reading the table (D6 only, 0.05° field, live IRSA data):
- **Alignment is good to a fraction of a pixel (6.15").** The target centroid on the
  common grid lands within ~0.5–1" of its proper-motion-predicted position for
  Barnard's Star, Epsilon Indi B and RW Aur. Resampling moves positions by ≤0.14"
  and conserves flux to within 0.5%.
- **WISE 0855** is faint at 4.4–5 µm in single frames, so centroids are noisier
  (1.9" median) and the native-vs-aligned flux ratio (0.93) is set by noise, not by
  resampling.
- **R Draconis is saturated in every D6 frame** (OVERFLOW and BLOOM at the target
  in 91 of 91). The masked core leaves no reliable native centroid ("n/a"), and the
  first-vs-last difference is dominated by saturation edges plus real Mira
  variability, hence σ ≈ 26. Use shorter-wavelength detectors or a larger
  `smooth_fwhm_arcsec` for this target, and treat its residuals as unreliable.
- A significance σ near 1 means the noise model is about right. Values below 1
  (Epsilon Indi B, WISE 0855) come from PSF-matching smoothing correlating the
  noise. The |S|>5 pixels for Barnard's Star are the expected motion dipole
  (≈10.3" ≈ 1.7 px in 362 days, pointing north).

## Testing

```bash
.venv\Scripts\python -m pytest
```

The tests use synthetic SPHEREx-like frames with known truth: arbitrary
rotations, Gaussian stars at exact sky positions, flags, NaNs, zodi. They
check that:
- reprojection puts stars within 0.1 px of the truth for rotations 0°, 37°, −161° and 152°, conserves flux to 2%, and keeps flag bits exact;
- mask profiles, background removal and the preservation of original data behave as specified;
- stack, difference and ratio maths and variance propagation are correct;
- a moving star produces the right +/− dipole while a stationary star largely cancels;
- wavelength matching and its caveats work;
- the FITS round trip, caching and every API route behave correctly.

**Wavelength maps (v1.2).** The WAVE extension and `wavelength` now come from the CWAVE/CBAND
Spectral WCS calibration for the image's pipeline version, verified against WCS-WAVE (see
`backend/data_acquisition/README.md`, "Releases, detectors and calibration files"). Each product
records `wavelength_source`, `wavelength_calibration` and `pipeline_version` (FITS keys `WAVESRC`,
`WAVECAL`, `PIPEVER`, `PSFFIX`). `wavelength` is the value **at the grid centre**: the linear
variable filter changes it by ~0.01 µm across a 3′ field (a quarter of a channel), so use the WAVE
plane at a source's pixel for anything off-centre.

**Spectral vs temporal axes.** A processed observation is one exposure: one time *and* a
per-pixel wavelength map. The 102 "channels" are labels for wavelength bins, not a time series:
grouping by channel combines different times, and a time series of one position mixes
wavelengths. Every comparison product (stacks, differences) warns when it combines both.

## Known limitations and scientific caveats

1. **The PSF is undersampled.** FWHM 7.5–9″ on 6.15″ pixels means a star's pixel values depend on where it falls within a pixel (in tests the peak varied by up to 40%). No resampling method removes this. Single-frame differences of bright stationary stars therefore leave residuals of ~10–40% of the peak. Stacking several frames per side averages this out; `smooth_fwhm_arcsec=15` cuts it to ~5–12% at the cost of resolution. Accurate positions and fluxes need PSF fitting with the `EPSF` extension (Workstream 4).
2. **Difference noise is above the propagated noise:** about 1.4× on Barnard's Star. Interpolation correlates neighbouring pixels and the variance is interpolated, not formally propagated through the resampling kernel. Treat significance maps as indicative, not as exact σ. Each comparison reports `significance_robust_std` so this is visible.
3. **The same detector does not mean the same wavelength.** D6 exposures of one target span 4.41–5.00 µm. Stacks across a detector, or comparisons between epochs observed at different wavelengths, mix spectral and temporal change. Use `match_wavelength=True`, or `channel` / `epoch_channel` grouping. Warnings are attached automatically.
4. **Reprojection does not fix bad astrometry.** If a frame's WCS is wrong, its aligned image is wrong by the same amount. Validation found no such case (offsets below), but no independent astrometric re-fit is done.
5. **Background is a constant** (zodi model + clipped median). This is right for small fields, but it will over- or under-subtract around extended emission (nebulae, galaxies, star-forming regions such as RW Aur's).
6. **PSF matching uses a Gaussian approximation** with the header's single `PSF_FWHM`. Real SPHEREx PSFs are not Gaussian and vary across the array.
7. **Saturated cores** (Barnard's Star, R Dra, RW Aur) are masked by `standard`. Use `mask_profile="minimal"` or the `RAW` plane to see them, but their values are not reliable.
8. **Channels are nominal:** 17 log-spaced bins per detector between the archive's limits. They are for grouping only; flux always uses each pixel's own wavelength (`WAVE`).
9. **Ratio images are only defined where B is significant** (S/N ≥ 5). On background-subtracted data that means on sources, not on empty sky.
10. **`/api/process` and `/api/compare` are synchronous.** First calls download cutouts (~4 MB each); cached calls are fast.
