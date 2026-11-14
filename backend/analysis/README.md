# SPHEREx analysis engine (Workstream 4)

Finds sources in a field's SPHEREx time series, follows them from visit to visit, measures
their motion, light curves and multi-channel spectra, checks reference catalogues and
gives each one a **preliminary, criteria-based** class. It works on Person 2's processed
observations (`backend.image_processing.process_series`), which come through Person 1's data
acquisition module. It downloads nothing raw itself and does no reprojection.

> Nothing this engine outputs is a discovery. A class is a *candidate* defined by the
> measured criteria listed with it; anything unusual needs independent verification.

## Contents

| File | What it does |
|---|---|
| `detection.py` | A. `detect_sources`, `extract_source_photometry`: matched-filter detection, pixel-integrated PSF fits, aperture photometry, MJy/sr → mJy |
| `matching.py` | B. visit stacks, registration, `match_sources`, `link_detections`, `find_tracklets` |
| `motion.py` | B/C. `calculate_angular_motion` (static / linear / quadratic fits, parallax), `predict_position` |
| `photometry.py` | D/E. `generate_light_curve`, `variability_indicator`, `generate_spectrum`, `compare_spectra` |
| `crossmatch.py` | F. `crossmatch_catalogue`, Gaia DR3 / CatWISE2020 / SIMBAD / SkyBoT |
| `classify.py` | G. `classify_candidate` with explicit tests and scores |
| `pipeline.py` | `analyse_observations`, `analyse_field` (cached), `identify_moving_candidates`, `to_viewer` |
| `../api/analysis_routes.py` | `GET /api/analysis`, `GET /api/analysis/crossmatch` |
| `../../scripts/validate_analysis.py` | live validation on the five targets → `examples/analysis/` |

## Quick start

```python
from backend.analysis import analyse_field
from backend.data_acquisition.coords import resolve_name, resolve_target

pos = resolve_target("Barnard's Star")
res = analyse_field(pos["ra"], pos["dec"], 0.05, target=resolve_name("Barnard's Star"))
for s in res["sources"]:
    print(s["source_id"], s["label"], s["confidence"], s["status"])
```

HTTP (the viewer calls this):

```
GET /api/analysis?ra=269.452&dec=4.693&fov=0.05[&target=Barnard's Star][&det=6][&catalogs=gaia,catwise,simbad][&full=false]
GET /api/analysis/crossmatch?ra=..&dec=..&radius=4&epoch=2026-03-01[&solar_system=true]
```

The response holds the viewer overlays `{sources, light_curves, spectra}` (see
`frontend/src/types.ts`) plus `results` (one record per source, below), `summary`,
`target` (measured vs ephemeris), `visits`, `registration_calibration`, `provenance`,
`warnings` and `disclaimer`.

## Output contract (one record per source)

```json
{
  "source_id": "S001", "ra": 269.4462, "dec": 4.7689, "ref_mjd": 61122.18,
  "detections": [
    {"kind": "visit_stack", "epoch": 0, "mjd": 60902.1, "ra": .., "dec": .., "ra_err_arcsec": 0.31,
     "registration_err_arcsec": 0.12, "ra_err_total_arcsec": 0.33, "flux_mjy": null, "quality_flags": ["masked_core", ...]},
    {"kind": "frame", "observation_id": "2025W33_2C_0321_1_D6", "mjd": .., "wavelength": 4.731, ...}
  ],
  "angular_motion": {"pm_ra_cosdec_mas_yr": -1474, "pm_dec_mas_yr": 10567, "pm_total_mas_yr": 10669,
                     "motion_sigma": 23.2, "linear_chi2_red": 0.24, "model": "linear", "baseline_yr": 1.0, ...},
  "motion_uncertainty": {"pm_ra_cosdec_err_mas_yr": 567, "pm_dec_err_mas_yr": 456, ...},
  "variability_indicator": {"sigma": .., "amplitude_mag": .., "reliable": true, "visits": [...]} ,
  "classification": "high_proper_motion_candidate", "label": "known high-proper-motion object",
  "confidence": 0.63, "status": "known",
  "known_catalogue_match": {"catalog": "gaia", "id": "Gaia DR3 4472832130942575872", "separation_arcsec": 0.19,
                            "pm_difference_sigma": 1.5, "pm_consistent": true},
  "criteria": {"high_proper_motion_candidate": {"passed": true, "confidence": 0.63, "criteria": [...]}},
  "light_curve": {...}, "spectrum": {...}
}
```

Every detection has a position, a time and an uncertainty (measurement, registration and
total). `status` is `known` (catalogue counterpart agrees) or `candidate` (needs verification).

## Method

**A. Detection and photometry.** The undersampled PSF (FWHM 7.5–9″ on 6.15″ pixels) is
modelled as a circular Gaussian *integrated over each pixel*, with FWHM from the frame's
`PSF_FWHM`. Detection uses a matched-filter S/N map (inverse-variance weighted; masked pixels
weigh zero), and peaks come from `photutils.find_peaks` at 5σ. If the variance plane
underestimates the noise, the S/N map is rescaled by its robust spread, measured away from bright
sources, but never boosted. Position and flux come from a weighted least-squares PSF fit on a 7×7
stamp, with covariance errors inflated by √χ²ᵣ when the fit is poor. Aperture photometry
(`photutils`, Gaussian aperture correction) is reported as a check. Flux density in mJy =
Σ surface brightness (MJy/sr) × solid angle of the grid pixel. The solid-angle map agrees with
the WCS pixel area to 0.04%, so this is exact after reprojection. **Saturated cores** (masked,
with saturation flags) are measured on the RAW plane with an intensity-weighted centroid, which
lands 0.5–0.7″ (median) from the catalogue position, versus 1–2″ for a PSF fit to the clipped
core. Their flux is *not* reported. Each source's wavelength is read from the frame's per-pixel
WAVE map (CWAVE calibration) at its position, not the frame centre. The filter's wavelength
changes by ~0.01 µm across a 3′ field.

**B. Temporal matching.** Frames are grouped into visits (Person 2's `assign_epochs`) and
stacked with Person 2's inverse-variance `stack_group`. Stacks reach ~√N deeper: WISE 0855 is
at 5σ in 9 of 45 single frames but in 3 of 4 visit stacks. Visit detections are linked across
visits by minimum-cost assignment (Hungarian). Each track predicts where its source should be,
from a linear fit once it has two visits, otherwise "here ± 12″/yr × Δt". The cost
(distance / gate) favours the smallest motion that fits the data. Every frame detection is then
attached to its track. Three error terms enter every position:

1. *measurement*: the scatter of the visit's frame detections (robust standard error) when there
   are ≥ 5, otherwise the fit error. Saturated cores get a 0.3″ floor.
2. *registration*: matched against low-motion Gaia stars (applied when ≥ 3 match); otherwise the
   visit-to-visit scatter of the field's own stationary sources is measured, in quadrature beyond
   their measurement errors (bounded 0.1–1.0″). Only if neither is possible is a 0.5″ floor assumed
   (and a warning given).
3. *undersampling / PSF*: a 0.05 px floor per frame and the √χ²ᵣ inflation of motion errors.

**C. Motion.** Weighted least squares on the tangent plane fits a static, a linear and (with
≥ 4 visits) a quadratic model. Motion significance is Δχ²(static − linear) with 2 d.o.f. When a
matched catalogue gives a parallax > 20 mas, the annual parallax (Earth barycentric position;
SPHEREx is in low Earth orbit) is removed before the fit. Non-linear apparent motion is
flagged by an F-test (p < 10⁻³ and χ²ᵣ > 3). Solar-system objects move ≥ 0.5″/h, against
< 0.002″/h for any star. They are searched within each visit among frame detections that no
stack source explains: ≥ 3 clean detections on a line at constant rate, away from saturated
stars. Known objects come from SkyBoT, using a short linear ephemeris from its position and rate.

**D. Variability.** Every frame is at a different wavelength, so flux vs time mixes the
spectrum with time. The indicator fits one smooth spectrum shared by all visits (a polynomial
in ln λ) times one scale factor per visit, then tests the scale factors against "constant".
Each scale error includes a 3% calibration-repeatability floor. "Variable" requires p < 10⁻³,
amplitude > 9%, ≥ 30% wavelength overlap between visits and every visit's flux at ≥ 5σ.
Visits are tagged with their release. DR1 + QR3 are treated as one uniformly processed set
(IRSA). Any other mix disables the test.

**E. Spectra.** Per-visit flux vs wavelength (raw points) and a channel-binned combined
profile on the 102 nominal channels, plus `compare_spectra` (ratio of two visits or two
sources on shared channels). This is a multi-channel PSF-photometry profile, not a calibrated
spectrum.

**F. Cross-matching.** One cone per field from Gaia DR3 and CatWISE2020 (`backend.catalogs`,
cached) and from SIMBAD TAP (CDS, with the Harvard mirror as fallback). Catalogue positions are
propagated with their own proper motion. For catalogue entries without a usable motion, the
measurement is propagated back with the measured motion, but only when that motion is ≥ 3σ.
Gates are capped (12″) so a badly measured source cannot "match" anything. CatWISE motions
flagged as suspect (next to saturated stars) are not used. Unavailable catalogues are listed
as unchecked.

**G. Classification.** Each class has explicit tests: value, threshold, "strong" value,
weight, required or not. A test's score rises linearly from 0 at its threshold to 1 at "strong".
Confidence = the weighted mean score × (0.5 + 0.5 × data quality), and a class is assigned only
when all required tests pass. Priority: possible solar-system object > transient > high proper
motion > variable > stationary.

| Class | Required tests (threshold → strong) |
|---|---|
| high_proper_motion_candidate | µ ≥ 500 mas/yr (→ 2000); motion ≥ 3σ (→ 15σ); µ/σµ ≥ 3 (→ 20); displacement ≥ 2″ (→ 6″); visits ≥ 2, ≥ 3 if saturated (→ 4); χ²ᵣ (not required) |
| variable_candidate | variability ≥ 3.3σ (→ 8σ); amplitude ≥ 9% (→ 50%); λ overlap ≥ 0.3; visits ≥ 2; min visit S/N ≥ 5; not saturated |
| transient_candidate | one visit only; S/N ≥ 10 (→ 30); ≥ 1 other visit covering the position; max S/N there ≤ 3; flux ≥ 3× the other visits' ceiling |
| possible_solar_system_object | ≥ 3 detections (→ 6) in one visit; rate ≥ 0.5″/h (→ 5); tracklet rms ≤ 2.5″ (→ 0.8″) |
| stationary | none of the above, ≥ 2 visits; confidence from visits and a non-significant motion |

Guards against false positives:

- A saturated source without any catalogue counterpart is never a mover or variable
  (`insufficient_data`, with the reason given).
- Unmatched sources on or within 40″ of a saturated star are flagged `saturation_artefact_risk`
  and their confidence is halved.
- A CatWISE "brown dwarf" subtype is only a hint (W1 − W2 ≥ 1.0).

## Validation on the five mandatory targets

Live run on 2026-11-14 (`scripts/validate_analysis.py`): D6 only, a 0.05° field, DR1 + QR3, wavelengths from CWAVE/CBAND.
Full outputs are in `examples/analysis/<target>.json`, and the viewer overlays in `<target>_viewer.json`.

| Target | Frames / visits | Target vs ephemeris (median) | Measured µ (mas/yr) | Catalogue µ | Δµ (σ) | Motion σ | Class (confidence) | Catalogue match |
|---|---|---|---|---|---|---|---|---|
| Barnard's Star | 53 / 4 | 0.44" | -1593 ± 572, 10354 ± 458 | -802, 10362 | 1.72 | 22.6 | known high-proper-motion object (0.62) | Gaia DR3 4472832130942575872 |
| Epsilon Indi B | 91 / 6 | 0.86" | 3913 ± 139, -2540 ± 146 | 3982, -2467 | 0.71 | 32.9 | known high-proper-motion object (0.90) | Gaia DR3 6412596012146801152 |
| WISE 0855 | 44 / 4 | 0.95" | -8717 ± 2017, 2414 ± 2357 | -8124, 673 | 0.90 | 4.0 | known high-proper-motion object (0.42) | CWISE J085506.91-071437.5 |
| RW Aurigae | 54 / 3 | 0.41" | 528 ± 885, -79 ± 832 | 7, -20 | 0.59 | 0.2 | stationary (0.38) | Gaia DR3 156430817820015232 |
| R Draconis | 85 / 3 | –" | – | – | – | – | not found (–) | – |

- **Barnard's Star.** The core is saturated, so the positions are RAW centroids, 0.44″ (median) from the
  Gaia-propagated ephemeris. µδ agrees with Gaia to 0.1%. µα* is 1.4σ off, because the year's baseline runs
  mostly north–south. The confidence (0.62) is lowered by the saturation quality factor. There is no photometry.
- **Epsilon Indi B.** This is the best-measured case: 6 visits, µ within 0.7σ of Gaia, 33σ motion. Ba and Bb
  (separated by 0.7″) form one source. Five nearby detections are either flagged as saturation artefacts or match
  catalogue entries.
- **WISE 0855.** Faint at 4.6 µm in single frames, detected in visit stacks. Registration could not be measured
  (no usable field stars), so a 0.5″ floor is assumed and the motion is only 4.0σ. It matches the CatWISE
  fragment J085506.91 (CatWISE splits this object into several entries) with consistent motion, and gets the
  subtype `possible_brown_dwarf` (W1 − W2 = 3.9).
- **RW Aurigae.** Stationary, as expected (µ ≈ 20 mas/yr is far below the sensitivity here), and 0.41″ from Gaia.
  The core is saturated, so there is no variability test. The field stars sit at the edges of the cutout and are
  rightly excluded as registration references.
- **R Draconis.** The core is saturated and masked in every D6 frame, so the target is not detected. All 9
  detections are flagged as saturation artefacts, and the field carries a warning. Shorter-wavelength detectors
  are needed. This failure is reported, not hidden.

No source in any field is reported as new. Every non-catalogue detection is labelled an artefact or a
low-confidence candidate that needs verification.

## Testing

`tests/test_analysis.py` (17 tests, synthetic data with known answers):

- detection position/flux recovery, and honest errors;
- per-pixel wavelength;
- forced photometry;
- linear proper motion, a static source, parallax removal, non-linear motion;
- linking a 10″/yr mover;
- asteroid tracklet vs noise;
- variability with a steep spectrum (constant vs brightening, release mixing);
- catalogue propagation;
- every classification rule;
- an end-to-end synthetic field;
- the API route;
- the PSF-header rule;
- the CWAVE lookup and its mismatch guard.

```
PYTHONPATH=. python -m pytest tests/test_analysis.py -q
PYTHONPATH=. python -m scripts.validate_analysis          # live, ~minutes
```

## Scientific limitations

- **Saturation.** At 4.4–5 µm, Barnard's Star, RW Aur and R Dra saturate in D6. Their positions
  come from RAW centroids (~0.5–1″), and they have **no photometry, light curve, spectrum or
  variability** here. R Dra is the extreme case: the masked core hides the star, and the
  "sources" around it are saturation artefacts (flagged). Shorter-wavelength detectors or
  non-saturated frames are needed.
- **Baseline and precision.** About one year of visits at ~6-month cadence with ~0.3–1″ per-visit
  errors gives σµ ≈ 0.15–2.5″/yr. Motions below ~1″/yr are not measurable, and a 3–5σ motion
  (WISE 0855 here) is a low-confidence candidate, not a measurement. Parallax is removed only
  when a catalogue provides it; for unknown nearby objects it inflates χ²ᵣ instead.
- **Confusion and blends.** On 6.15″ pixels, close pairs (RW Aur A/B at 1.4″; Eps Indi Ba/Bb)
  are one source. Linking can swap neighbours when a field is crowded.
- **PSF model.** A Gaussian of the header FWHM approximates the real, undersampled EPSF. PSF
  fluxes can be off by ~10% in absolute terms, which matters less for ratios within one detector.
  The EPSF extension is not used; if it ever is, the QR PSF header bug (pipeline 6.4–6.5.5) must
  be corrected first (`psf_header_fix_needed`).
- **Variability is the hardest claim.** Different wavelengths, detector positions, pixel phase,
  background and calibration between visits can all imitate it. The indicator removes a smooth
  spectrum and adds a 3% floor. Sharp spectral features (e.g. CH₄ at 3.3 µm, CO at 4.6 µm)
  sampled differently in each visit can still leak in. Repeat in another channel before
  believing it.
- **Spectra** are PSF-photometry profiles from frames taken at different times. They are not
  calibrated spectra, and a variable source blurs the combined profile.
- **Solar-system search** only finds objects that move ≥ 0.5″/h within one visit with ≥ 3 clean
  detections. Slower or single-detection objects become orphans. SkyBoT is geocentric, so
  parallax for near-Earth objects is ignored.
- **Catalogues.** "No counterpart" means "none in the catalogues that answered". CatWISE2020
  splits very fast movers into several entries (WISE 0855 has four), and its motions near bright
  stars are often spurious (flagged).
- **Small fields.** The viewer's 0.05° field rarely holds ≥ 3 Gaia stars bright at 4–5 µm, so
  registration usually comes from the field's own stationary sources. With fewer than two such
  sources, a 0.5″ floor is assumed and stated.
