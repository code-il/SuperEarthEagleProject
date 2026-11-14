# Demonstration scenarios

Start the app with `python -m scripts.start` and open http://localhost:8000.

The first visit to a field downloads its cutouts from IRSA, which takes 1–3 minutes. Before a live
demo, run `python -m scripts.demo_workflow` once so every target is cached; after that each target
opens in seconds.

## Scenario 1: WISE 0855, the coldest known brown dwarf (the acceptance workflow)

| # | Do | What to show / say |
|---|---|---|
| 1 | Open the home page | What SPHEREx is; the five challenge targets |
| 2 | Click **WISE 0855**, or "or at RA 08h55m10.831s Dec −07d14m42.53s" for the supplied coordinates | The name is resolved by CDS Sesame with its proper motion. The coordinate link uses exactly the challenge's position, but in a 0.1° field: those coordinates are from the AllWISE epoch, and at 8″/yr the object is ~2′ (≈130″) away in 2025–26, outside a 0.05° field. This is proper motion in action |
| 3 | Wait for loading | The green badge: *N observations · M visits · first → last date*, all SPHEREx frames covering the spot |
| 4 | The timeline | Frames in time order. Set **Axis** to "true time (linear)" to see the real ~6-month gaps, or "by visit" to spread each visit out |
| 5 | Press **Space** (play) | The time-lapse. Point out that each frame is at a different wavelength (the λ readout changes) |
| 6 | Press **Space** again (pause) | Pick a frame. The right panel shows date, λ at centre, bandwidth, λ calibration (CWAVE/CBAND), detector, release (DR1/QR3), PSF, quality flags |
| 7 | Mode **Blink**, then **Side by side** | Compare two dates. B is chosen automatically (another visit, closest wavelength) or pinned with **B** |
| 8 | Mode **Difference** (σ view) | WISE 0855 leaves a faint light/dark pair; stationary stars cancel. Read the caveats |
| 9 | Overlays: **Gaia**, **CatWISE** | Catalogue stars moved to each frame's date. CatWISE lists WISE 0855 in pieces (a known catalogue limitation) |
| 10 | **Analysis** panel | Sources detected in every visit; the ★ target |
| 11 | Click the ★ source | Measured position, proper motion ± error and significance, catalogue match and the "Why this class?" criteria. The light curve and spectrum load below |
| 12 | **Copy link** | The URL reopens this exact view (target, field, frame, mode). Paste it in a new tab |

What to expect: WISE 0855 is faint at 4.6 µm. It is detected in visit stacks, with motion
consistent with its catalogue value (~8″/yr) at only 3–5σ, so it shows as a low-confidence known
object. That is an honest result, not a failure.

## Scenario 2: Barnard's Star, the fastest-moving star

Open it from the home page, play the time-lapse (Axis "by visit") and watch the bright star step
north by ~10″ per year. Then:

- In **Difference** mode the pair is strong.
- In the Analysis panel the ★ source moves ~10.4″/yr at >20σ, matching Gaia.
- The star is **saturated** at these wavelengths. The panel says so, and there is no light curve.
  This shows that the app reports what it cannot measure.

## Scenario 3: Epsilon Indi B, a nearby brown-dwarf binary

This is the cleanest measurement: 6 visits, ~4.7″/yr, more than 30σ, and within ~1σ of Gaia.
Point out "Parallax removed ≈ 270 mas": at 3.6 pc its yearly parallax wobble is taken out before
fitting the motion.

The light curve shows how flux changes with wavelength. The variability test says "not testable"
because each visit covers different wavelengths. Explain why that is the right answer.

## Scenario 4: RW Aurigae, a young variable star

It is stationary within errors, as expected. Use **Spectral channels** to keep one wavelength and
play: real variability should be compared at matched wavelengths. The star saturates in D6, so the
app gives no brightness measurement. Load D1–D3 with the detector buttons for unsaturated data;
this takes more downloads.

## Scenario 5: R Draconis, a Mira variable and a failure case

The home-page card opens a wider field. The star is so bright at 4–5 µm that its core is masked in
every D6 frame. The analysis does not find the target, and it flags every detection nearby as a
probable saturation artefact, with a field warning. Use this to explain artefacts and why a
"candidate" is not a discovery (About & help).

## Live run of the acceptance workflow

Run on 2026-11-14 against a local server (app 1.0.0, processing 1.2, analysis 1.1), live IRSA data, detector D6. Full step-by-step report: [examples/demo/demo_report.md](../examples/demo/demo_report.md).

| Target | Steps passed | Observations (visits) | Step 11: measured position & motion |
|---|---|---|---|
| Barnard's Star | 12/12 | 57 frames, 4 visits | S001 at RA 269.44624 Dec 4.76889, 0.53" from ephemeris; µ = (-1467 ± 567, 10567 ± 456) mas/yr, 23.2σ; known high-proper-motion object (0.62); match Gaia DR3 4472832130942575872 |
| Epsilon Indi B | 12/12 | 92 frames, 6 visits | S001 at RA 331.09681 Dec -56.80084, 0.948" from ephemeris; µ = (3850 ± 145, -2547 ± 146) mas/yr, 31.7σ; known high-proper-motion object (0.90); match Gaia DR3 6412596012146801152 |
| WISE 0855 | 12/12 | 44 frames, 4 visits | S013 at RA 133.75917 Dec -7.24200, 1.157" from ephemeris; µ = (-6549 ± 1785, 2947 ± 1401) mas/yr, 3.8σ; known high-proper-motion object (0.42); match CWISE J085506.91-071437.5 |
| RW Aurigae | 12/12 | 54 frames, 3 visits | S001 at RA 76.95650 Dec 30.40125, 0.413" from ephemeris; µ = (528 ± 885, -78 ± 832) mas/yr, 0.2σ; stationary (0.38); match Gaia DR3 156430817820015232 |
| R Draconis | 11/12 | 90 frames, 3 visits | Target not detected within 20" of its predicted position (faint, saturated or masked). |

**59 of 60 steps passed.** The failure is R Draconis step 11: the star saturates and is masked in every D6 frame, so the analysis cannot find it and says so. That is the honest result, not a software fault. Every other target is found within ~1.2″ of its catalogue-predicted position. Moving targets have motions consistent with Gaia/CatWISE (WISE 0855 at only 3.8σ), and RW Aur is stationary. WISE 0855 was run at the challenge's supplied coordinates in a 0.1° field (see Scenario 1).

## Known limitations

**Data and archive**
- The first visit to a field downloads 50–100 cutouts from IRSA, which takes 1–3 min. IRSA
  sometimes answers HTTP 503 under load; after retries, failed frames are skipped and listed in
  *Data quality*.
- The default is detector D6 (4.4–5.0 µm), where cool brown dwarfs are brightest. A position is seen
  by one or two detectors per exposure, never all six at once, and other detectors mean more
  downloads.
- Releases DR1 + QR3 are used together, as IRSA intends. The analysis turns its variability test off
  for any other mix.

**Science**
- Bright stars saturate at 4–5 µm. Their positions come from RAW centroids (~0.5–1″), with no
  photometry, light curve or spectrum; R Dra is not detected.
- The baseline is about one year of visits with 0.3–1″ per-visit errors. Motions under ~1″/yr are not
  measurable, and faint fast movers reach only a few σ.
- Each frame is at a different wavelength, so brightness changes mix colour and time. Compare at
  matched wavelengths; the variability test models a smooth spectrum and refuses when visits don't
  overlap in wavelength.
- 6.15″ pixels blend close pairs (RW Aur A/B, Eps Indi Ba/Bb).
- Source ids (S001…) are per field. A slightly different centre, for example the name-resolved
  position at the reference epoch vs fixed coordinates, can change sources near the field edge.
- Classifications are criteria-based and preliminary. "Known" means a catalogue entry agrees;
  "candidate" means no catalogue explains it, and is never a discovery.

**Application**
- The rate limiter, job queue and in-memory analysis cache live in one server process. Use one
  worker per container and a proxy limiter for public scale (DEPLOYMENT.md).
- The viewer downloads every processed FITS frame of a field (about 0.1 MB each) to stretch and
  difference them in the browser. Slow connections take a while for 100-frame fields.
- The sky-context panel (Aladin Lite) and its HiPS tiles load from CDS/IRSA directly and need
  internet access.
- Plots load Plotly (~1.4 MB gzipped) on demand.
- The Docker image was written but not built in the development environment (Docker was not
  running). The same steps run in `scripts/start.py`, which was tested.
