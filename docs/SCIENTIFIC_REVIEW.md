# Scientific review across the five workstreams

*Review date 2026-11-14. Reviewer: the analysis workstream, acting as cross-component
scientific reviewer.*

For each component, the question was whether the code calculates what it is supposed to,
checked independently on real IRSA products rather than taken from the code's own comments.
Each item below gives the check, the evidence and the status.

## 1. 102 spectral channels are not 102 images in time

**What should hold.** A SPHEREx field is visited about every 6 months. Each visit is a burst of
frames taken over hours to days, and each frame sees the field through a different part of the
linear-variable filter, so at a different wavelength. Time and wavelength are two independent
axes.

**What we verified.**

- *Acquisition (P1)* returns one record per frame with `mjd` and `wavelength` as separate fields.
- *Processing (P2)* groups frames into epochs (visits) by time with `assign_epochs`. Its
  per-channel products bin by wavelength *within* the data, not as a time axis.
- *Analysis (P4)* uses both axes. Motion uses visit epochs. Variability fits one spectrum shared
  by all visits plus one scale factor per visit, so a spectral slope is not mistaken for a change
  in brightness (unit test `test_constant_source_with_steep_spectrum_is_not_variable`). Spectra are flux vs
  wavelength per visit.

The filter's wavelength also changes *within* a frame, by about 0.0095 µm across a 3′ cutout,
so sources now take the wavelength at their own pixel from the per-pixel map.

**Status: correct; tightened (per-pixel wavelength).**

## 2. Pixels are MJy/sr surface brightness, not calibrated flux

**What should hold.** Flux density = Σ(surface brightness × pixel solid angle) over the source's
PSF/aperture.

**What we verified.** `detection.mjysr_to_mjy` uses the solid angle of the reprojected grid
pixel. Against IRSA's solid-angle pixel map (`cal-sapm-v3-2026-191`, D6), the WCS-derived area
agrees to **0.04%**. Light curves report mJy and AB magnitudes from PSF photometry, with aperture
photometry as a cross-check. The viewer labels images in MJy/sr.

**Status: correct.**

## 3. Use CWAVE/CBAND for science, not WCS-WAVE

**What should hold.** The `WCS-WAVE` lookup table in each image is meant for visualisation. The
per-detector Spectral WCS calibration (CWAVE/CBAND) is the science product, and its version must
match the image's.

**What we found.** With the matching file (`cal-swcs-v5-2026-191`, used by DR1/QR3),
CWAVE − WCS-WAVE has a median of 0.0003 µm. That is within the lookup error (p99 0.0013 µm).
With the older v4 file the offset is **0.0067 µm**, enough to move 20% of frames into a different
nominal channel. Using "a" CWAVE file is therefore not enough; it has to be the right one.

**Fix.** `backend/data_acquisition/calibration.py` selects the calibration from the pipeline
version, maps cutout pixels to detector pixels through the cutout's `A` WCS, and checks itself:
if CWAVE and WCS-WAVE differ by more than 3 × WLERRP99, the image keeps WCS-WAVE and says so.
Processing v1.2 records `WAVESRC`, `WAVECAL`, `PIPEVER` and `PSFFIX` in every product. The viewer
shows "λ at centre", "Bandwidth" and "λ calibration".

**Status: fixed (P1/P2), shown in the viewer (P3), used by the analysis (P4).**

## 4. Not every position is covered by all six detectors

**What should hold.** Each detector sees a different part of the sky in a given exposure. A
position is covered by at most two detectors per exposure (the filter-boundary overlap), often
by only one.

**What we verified.** Across the cached series, at most 2 detectors cover a position per
exposure, and 3% of exposures have only 1. No code assumes six. The acquisition layer queries
per detector, the viewer defaults to D6 (which covers 4.4–5.0 µm) and labels it, and the
analysis takes `det=` and reports frames per detector.

**Status: correct; documented.**

## 5. Make the data release explicit and compare consistent releases

**What should hold.** DR1 (released November 2026) is a reprocessing of QR2. IRSA states that
DR1 and QR3 (released September 2026) are a uniformly processed set intended to be used together.
QR1/QR2 used older pipelines and calibrations.

**What we verified.** The defaults use DR1 + QR3 only, and QR2 is excluded. Every frame,
detection, light-curve point and spectrum point carries `data_release`. The analysis
`provenance` lists the releases and pipeline versions (all cached images: 7.0.5). The
variability test treats DR1/QR3 as consistent and **disables itself** for any other mix, with a
warning (unit test `test_brightening_is_detected_and_release_mixing_handled`).

**Status: correct; release tagging extended to all outputs.**

## 6. The PSF header issue in QR products before 2026-03-31

**What should hold.** Pipeline versions 6.4–6.5.5 without "+psffix1" write the EPSF zone planes
in the wrong order (correct plane = y × n_cols + x + 1).

**What we verified.** All cutouts used here are pipeline 7.0.5, which is not affected. The
analysis does not use the EPSF extension: it uses a Gaussian of the header FWHM. A guard,
`psf_header_fix_needed(version)`, flags any affected product, and it is recorded in
`provenance`, so a future EPSF user knows when to reorder the planes.

**Status: not affected; guarded.**

## Other findings from the review

| Component | Finding | Action |
|---|---|---|
| P2 processing | Saturated cores (Barnard's Star, RW Aur, R Dra in D6) are masked; a PSF fit on them is biased by 1–7″ | Analysis uses RAW centroids for saturated cores and gives them no photometry |
| P3 viewer | Wavelength shown to 4 decimals without saying it is the frame centre | Changed to "λ at centre", 3 decimals |
| P4 analysis | Bright-star halos produced spurious "sources" and a fake asteroid tracklet | Artefact-risk flag within 40″ of saturated stars; tracklets only from clean detections |
| P4 analysis | Catalogue gates could grow to match unrelated stars 30–130″ away | Measured-motion propagation only when motion ≥ 3σ; gate capped at 12″ |
| P1 catalogues | CatWISE splits WISE 0855 into several entries; motions next to saturated stars are spurious | Documented; suspect motions not used |

## Who validates what

| Person | Validates against reference data |
|---|---|
| P1 acquisition | Releases and pipeline versions; calibration file selection; cutout ↔ detector mapping; coverage per detector |
| P2 processing | Units (MJy/sr), solid angle, CWAVE vs WCS-WAVE, masks/saturation, epoch grouping |
| P3 viewer | Labels and units shown match the products (wavelength centre/bandwidth, release, detector) |
| P4 analysis | Motion vs Gaia/ephemerides, photometry vs catalogue fluxes, spectra, candidate criteria and confidence |
| P5 integration | Automated tests (`pytest`, `vitest`), the live validation script, validation reports |

Live validation results are in `examples/analysis/validation_summary.md`. The method and its
limitations are in `backend/analysis/README.md`.
