# SPHEREx data acquisition engine (Workstream 1)

Python module + FastAPI routes that take a sky position (or an object name), find
every NASA SPHEREx spectral image covering it in the IRSA archive, and download
real FITS cutouts with their full metadata. It is the data source for image
processing (Workstream 2), the viewer (3) and the analysis engine (4).

No frontend, no image alignment/differencing, no source detection: those live in
the other workstreams.

## Contents
- [Setup](#setup)
- [Python interface](#python-interface)
- [HTTP API](#http-api)
- [Record format (integration contract)](#record-format-integration-contract)
- [How it works](#how-it-works)
- [Adding a new data release](#adding-a-new-data-release)
- [Testing](#testing)
- [Verified results for the 5 mandatory targets](#verified-results-for-the-5-mandatory-targets)
- [Known limitations](#known-limitations)

## Setup

Python 3.11. From the project root:

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

(On macOS/Linux use `.venv/bin/python`.) No API keys or credentials are needed:
IRSA and CDS are public.

Run the API:

```bash
.venv\Scripts\python -m uvicorn backend.api.main:app --reload --port 8000
```

Interactive docs: http://localhost:8000/docs

### Configuration (environment variables, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `SPHEREX_CACHE_DIR` | `./cache` | Query cache, record index, cutouts, full images |
| `SPHEREX_DEFAULT_RELEASES` | `DR1,QR3` | Releases searched when none is given |
| `SPHEREX_RELEASES_FILE` | none | JSON list of releases (see below) |
| `SPHEREX_QUERY_CACHE_TTL_S` | `21600` (6 h) | Age after which archive queries are re-run |
| `SPHEREX_HTTP_RETRIES` / `SPHEREX_HTTP_BACKOFF_S` | `6` / `1.5` | Retry policy (IRSA often returns 503 "Server busy") |
| `SPHEREX_MAX_WORKERS` | `6` | Parallel cutout downloads |
| `SPHEREX_MAX_FOV_DEG` / `SPHEREX_MIN_FOV_DEG` | `2.0` / `0.005` | Allowed field of view |
| `SPHEREX_MAX_CUTOUTS_PER_REQUEST` | `400` | Guard against huge batches |
| `SPHEREX_REFERENCE_EPOCH` | `2026-03-01` | Date named targets are propagated to for the search centre |
| `SPHEREX_EPOCH_GAP_DAYS` | `20` | Gap that starts a new survey epoch when grouping |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Allowed browser origins for the API |

## Python interface

```python
from backend.data_acquisition import (
    resolve_target, search_observations, get_observation_metadata,
    get_available_observation_dates, get_available_bands,
    download_cutout, download_cutouts, download_observation,
)

pos = resolve_target("WISE 0855", epoch="2026-03-01")  # or "08 55 10.831 -07 14 42.53"
recs = search_observations(pos["ra"], pos["dec"], fov=0.05,
                           date_range=("2025-01-01", None),   # optional
                           release="DR1,QR3",                  # optional, or "all"
                           wavelength_range=(4.0, 5.0),        # optional, micrometres
                           detectors=[6])                      # optional, 1-6
batch = download_cutouts(recs, pos["ra"], pos["dec"], fov=0.03)
for r in batch.records:
    print(r.observation_time, r.wavelength_at_target, r.quality_flags, r.local_path)
```

| Function | Purpose |
|---|---|
| `search_observations(ra, dec, fov, date_range, release, wavelength_range, detectors)` | All images overlapping the region, chronological, de-duplicated across releases |
| `get_observation_metadata(observation_id)` | One record; from the local index, else IRSA TAP + SIA2 |
| `download_cutout(access_url, ra, dec, fov)` | One cutout (cached); returns path + metadata read from the FITS |
| `download_cutouts(records, ra, dec, fov)` | Parallel batch; collects failures, skips images not containing the position |
| `download_observation(access_url, output_directory)` | Whole 2040×2040 image (~60 MB), unchanged |
| `get_available_observation_dates(ra, dec, fov)` | Distinct dates + survey epochs |
| `get_available_bands(ra, dec, fov)` | Detectors D1-D6 present, with wavelength coverage |
| `resolve_target(text)` / `resolve_name(name)` | Name or coordinate text to position (with proper motion) |
| `parse_coordinates(ra, dec)` | Shared coordinate convention (below) |

**Coordinate convention.** Numbers are always decimal degrees. Sexagesimal RA
is in hours (`08h55m10.831s`, `08:55:10.831`, `08 55 10.831`), Dec in degrees
(`-7d14m42.53s`, `-07:14:42.53`). Invalid input raises `CoordinateError`
(HTTP 400).

## HTTP API

Every position can be given as `ra` + `dec` or as `q` (name or coordinate
string). Routes follow the suggested integration layout and are provisional
until agreed with Person 5.

| Method & path | Description |
|---|---|
| `GET /api/health` | Status |
| `GET /api/releases` | Configured data releases |
| `GET /api/targets` | The 5 mandatory targets (resolved live by name) |
| `GET /api/resolve?q=` | Name/coordinates to position; `epoch=` propagates proper motion |
| `GET /api/observations?q=&fov=&date_start=&date_end=&release=&wavelength_min=&wavelength_max=&detectors=` | Search: records + epochs |
| `POST /api/search` | Same, JSON body |
| `GET /api/observations/dates` | Dates and epochs |
| `GET /api/observations/bands` | Detector bands |
| `GET /api/observations/{observation_id}` | One record |
| `POST /api/cutouts` | Download cutouts for a search (`observation_ids`, `track_target`, `detectors`...); returns enriched records with `file_url` |
| `GET /api/files/cutouts/{path}` | Serve a cached cutout FITS (or its `.json` sidecar) |

Example:

```bash
curl "http://localhost:8000/api/observations?q=WISE%200855&fov=0.05&detectors=6"
```

```bash
curl -X POST http://localhost:8000/api/cutouts -H "Content-Type: application/json" -d "{\"q\": \"Barnard's Star\", \"fov\": 0.03, \"detectors\": [6], \"date_end\": \"2025-09-30\"}"
```

For named targets, every record also carries `target_predicted_ra/dec`: where
the object should be at that exposure's time according to its catalogue proper
motion. Workstream 4 can use it to verify motion; the viewer can draw it.
`track_target: true` centres each cutout on that predicted position (the object
stays centred). The default fixed centre is what a blink comparison needs.

Errors: `400` invalid input · `404` unknown ID/file · `502` archive failure.

## Record format (integration contract)

Real record (truncated) from `examples/wise-0855_cutouts.json`:

```json
{
  "observation_id": "2025W19_1B_0116_1_D6",
  "observation_time": "2025-05-05T16:48:02.419Z",
  "ra": 134.40997823066974,
  "dec": -9.023376583067057,
  "wavelength_min": 4.4115,
  "wavelength_max": 5.0096,
  "band": "SPHEREx-D6",
  "detector": 6,
  "data_release": "DR1",
  "calibration_level": 2,
  "quality_flags": [],
  "fits_url": "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/.../level2_2025W19_1B_0116_1D6_spx_l2b-v27-2026-197.fits",
  "local_path": ".../cache/cutouts/level2_2025W19_1B_0116_1D6_spx_l2b-v27-2026-197/cut_133.75940_-7.24215_0.0300.fits",
  "mjd_start": 60800.700028,
  "exposure_time_s": 113.5826,
  "pixel_scale_arcsec": 6.15,
  "covers_position": true,
  "wavelength_at_target": 4.9674,
  "bandwidth_at_target": 0.0372,
  "quality": {"l1_dqa": "Pass", "l2_dqa": "Pass", "psf_fwhm": 9.04,
              "target_pixel_flags": ["SOURCE"], "fraction_with_problem_flags": 0.0216, "...": "..."},
  "cutout": {"center_ra": 133.7594, "center_dec": -7.24215, "size_deg": 0.03, "sha256": "...", "...": "..."}
}
```

Notes on fields:
- `observation_id` = `<obs_id>_D<detector>`: one exposure on one detector array. It is unique, and identical across releases (de-duplication key).
- `ra`/`dec` are the pointing centre of the full 3.5° image, not the search position. The cutout centre is in `cutout`.
- `wavelength_min/max` span the detector's whole linear variable filter. The wavelength the target was actually observed at differs per exposure and is `wavelength_at_target`, read from the file's spectral WCS after download. A single image is one narrow slice of the target's spectrum: building a spectrum means combining many exposures.
- `quality_flags` lists the problem flags in the 3×3 pixels around the target, decoded from the FLAGS plane using the bit definitions stored in that file's own header, plus any non-`Pass` L1/L2 QA verdict. `FULLSAMPLE` and `SOURCE` are informational, so they are excluded. The field is empty until a cutout is downloaded (the archive search has no per-pixel flags). Missing values are always `null`, never guessed.
- `covers_position: false` means the image only clips the edge of the search circle. IRSA cannot cut it out at the target.

## How it works

1. **Search**: IRSA SIA2 (`https://irsa.ipac.caltech.edu/SIA`, via PyVO) per release collection, in parallel; circle of diameter `fov`; optional time/band constraints. Rows are cached on disk for 6 h (~2–5 s uncached, instant cached).
2. **Normalise**: rows become records (µm, ISO UTC, detector parsed). The same exposure in two releases keeps the higher-priority one (DR1 > QR3). Records are sorted by time, then detector. Each image's footprint polygon is tested for containing the position.
3. **Cutouts**: `<access_url>?center=<ra>,<dec>d&size=<deg>`. IRSA returns the full multi-extension FITS (IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE) with all headers intact. The download is streamed to a temp file, checked (length, FITS signature, IMAGE HDU) and only then moved into place. Per-file locks prevent duplicate parallel downloads. A JSON sidecar stores provenance (URL, sha256, retrieval time) and the extracted metadata.
4. **Names**: CDS Sesame (SIMBAD, then NED/VizieR) gives J2000 position, proper motion, parallax and radial velocity. Positions are propagated with `SkyCoord.apply_space_motion`. This matters: Barnard's Star has moved ~4.5′ since J2000, more than a typical cutout.

## Adding a new data release

Nothing in the downloader changes. Either add a `Release(...)` to
`DEFAULT_RELEASES` in `config.py`, or create a JSON file and point
`SPHEREX_RELEASES_FILE` at it:

```json
[
  {"key": "DR2", "collection": "spherex_dr2", "priority": 30, "deep": false, "description": "Data Release 2"},
  {"key": "DR1", "collection": "spherex_dr1", "priority": 20}
]
```

Then set `SPHEREX_DEFAULT_RELEASES=DR2,DR1` if it should be searched by
default. New weekly quick-release data shows up once the 6-hour query cache
expires (or with `use_cache=False`).

## Testing

```bash
.venv\Scripts\python -m pytest
```
62 offline unit tests (no network: SIA2, Sesame and HTTP are mocked with
synthetic SPHEREx-like FITS). They cover coordinate parsing, validation,
proper-motion propagation, de-duplication, caching, retries on 503, truncated
or non-FITS downloads, flag decoding, footprint tests and every API route.

```bash
.venv\Scripts\python -m pytest -m live
```
2 live tests against IRSA/CDS (resolve WISE 0855, search, bands, one real cutout, metadata round trip).

```bash
.venv\Scripts\python -m scripts.fetch_targets
```
Reproducible end-to-end procedure for the five mandatory targets. It resolves
each target, searches, lists epochs and bands, and downloads real cutouts (D6
per epoch + one D1). It checks the brightest pixel against the predicted
position and does a cold TAP lookup by ID. Output goes to `examples/`
(`*_search.json`, `*_cutouts.json`, `live_summary.json`). The downloaded FITS
files are in `cache/cutouts/`.

## Verified results for the 5 mandatory targets

Run on 2026-11-14 (fov 0.05° search, 0.03° cutouts, DR1 + QR3):

| Target | Images | Covering position | Date span | Epochs | Peak-pixel offset from predicted position (D6) |
|---|---|---|---|---|---|
| Barnard's Star | 377 (DR1 250, QR3 127) | 358 | 2025-08-15 → 2026-09-22 | 5 | 4.6″, 1.7″, 2.4″ |
| Epsilon Indi B | 472 (DR1 352, QR3 120) | 458 | 2025-04-24 → 2026-10-02 | 4 | 3.8″, 0.8″, 4.9″, 3.8″ |
| WISE 0855 | 259 (DR1 only) | 258 | 2025-05-05 → 2026-05-14 | 4 | 26.3″\*, 4.0″, 1.6″, 5.2″ |
| RW Aurigae | 339 (DR1 227, QR3 112) | 335 | 2025-09-01 → 2026-09-16 | 3 | 2.6″, 2.5″, 2.3″ |
| R Draconis | 555 (DR1 428, QR3 127) | 539 | 2025-05-16 → 2026-07-16 | 3 | 10.3″, 8.4″, 7.1″\*\* |

Pixels are 6.15″, so most checks agree to within one pixel. This confirms
that archive times, the WCS and proper-motion propagation are consistent. For
the three fast movers the J2000 catalogue position is more than 30″ away from
the star in every image, so propagation is essential. Archive `t_min` agrees
with each file's `DATE-OBS` to <0.1 s. TAP look-ups by ID returned records
identical to search results for all five targets.

\* WISE 0855 is faint in single exposures; in that image the brightest pixel nearby is a different source.
\*\* R Dra is saturated (`OVERFLOW`, `BLOOM` flags), which blurs the peak; RW Aur and Barnard's Star are flagged `OVERFLOW` too.

## Releases, detectors and calibration files (scientific review, 2026-11-14)

- **Releases are explicit.** Every record carries `data_release`. The default search is
  `DR1` + `QR3`: IRSA states that "the DR1 spectral images are intended to be used together
  with the QR3 spectral images as a uniformly processed set" (DR1 is a reprocessing of QR2).
  QR2 is not searched, so it can never be mixed in silently. All cached images were made by
  pipeline `VERSION 7.0.5`.
- **A sky position is seen by at most two detectors per exposure** (one short-wave D1–D3 and
  one mid-wave D4–D6, through the dichroic), never all six. In the five target fields, 3% of
  exposures cover the position with only one detector. Nothing here assumes six.
- **Per-pixel wavelengths for science come from the Spectral WCS calibration product**
  (CWAVE/CBAND), not from the image's WCS-WAVE table, which is an approximation "intended for
  visualization". `calibration.py` downloads the per-detector file matching the image's
  pipeline version (R7 / pipeline 7.x → `cal-swcs-v5-2026-191`, used by DR1 and QR3; earlier →
  `cal-wcs-v4-2025-254`) and maps each cutout pixel to its detector pixel with the cutout's
  `A` WCS ("0-based active pixel coordinates"). It then checks the choice: CWAVE must agree with
  the image's own WCS-WAVE to within 3x the lookup error in the file header (`WLERRP99` =
  0.0013 µm). Measured on 362 D6 cutouts: median |CWAVE − WCS-WAVE| = 0.0003 µm with the v5
  file, but 0.0067 µm (systematic) with the v4 file, enough to change the channel of 20% of
  frames. So the calibration *version* matters far more than the lookup approximation. Set
  `SPHEREX_USE_SPECTRAL_WCS_CAL=0` to disable this (offline use).
- **Pixel solid angle.** Images are in MJy/sr. The solid-angle pixel map
  (`cal-sapm-v3-2026-191`, 37.5–38.5 arcsec² across D6) agrees with the pixel area implied by
  each image's SIP WCS to 0.04% (p5–p95). Photometry that sums MJy/sr x (solid angle of the
  grid pixel) is therefore correct after reprojection.
- **PSF header bug.** IRSA notice (2026-03-31): QR images from pipeline 6.4–6.5.5 store the
  EPSF planes in the wrong order (correct plane = y x n_cols + x + 1). Images record
  `psf_header_fix_needed`; it is False for every cached image (7.0.5). The engine only uses the
  scalar `PSF_FWHM`; code that reads the EPSF extension must re-index it when the flag is True.

## Known limitations

1. **IRSA cutout service is intermittently busy.** It answers `503 Server busy`. Requests retry with back-off (~95 s total) and are then reported as failures, not raised. In the verification run 1 of 22 cutouts (a QR3 D6 image of Barnard's Star) kept returning 503 while other files worked. Re-running fetches only what is missing, because results are cached.
2. **Cutouts are ~4 MB each, whatever their size,** because IRSA always includes the EPSF table (441 PSFs × 33×33 doubles). A full time series for one position (~400 images) is ~1.5 GB, so filter by `detectors` / dates where possible.
3. **Wavelength at the target is only known after download.** The archive search gives the detector-wide range.
4. **`quality_flags` are empty until a cutout is downloaded.**
5. **Bright targets are saturated** in many images (Barnard's Star, R Dra, RW Aur: `OVERFLOW`/`BLOOM`). Flux at the target pixel is unreliable there; downstream photometry must check `quality_flags`.
6. **Proper-motion propagation is barycentric.** It does not include the annual parallax wobble (up to ±0.55″ for Barnard's Star, well under a pixel). Sesame positions are assumed to be at epoch J2000 (SIMBAD's convention).
7. **Name resolution uses CDS Sesame over HTTP.** astroquery's SIMBAD client failed on 2026-11-14 (capabilities endpoint 404), so it is not used. If Sesame is unreachable, only WISE 0855 has a fallback (the challenge-supplied coordinates, whose epoch is not stated).
8. **Images that only clip the search circle** (`covers_position: false`, ~3–5 %) are skipped for cutouts; IRSA returns HTTP 422 for them.
9. **Epoch grouping is a heuristic** (gap > 20 days). It produces a few small "epochs" (e.g. 8–11 images) between the main survey passes.
10. **Cold `GET /api/observations/{id}` takes 12–30 s** (IRSA TAP). After any search it is instant (local index).
11. **`POST /api/cutouts` is synchronous.** Large uncached batches can take minutes. There is no job queue yet; if the integrated app needs one, that is a Workstream 5 decision.
12. **Deep-field collections** (`DR1_DEEP`, `QR3_DEEP`) contain only the ecliptic-pole deep fields and returned nothing for these targets. They are configured but not searched by default.
13. **WISE 0855 has no QR3 images yet,** so its series ends in May 2026.
