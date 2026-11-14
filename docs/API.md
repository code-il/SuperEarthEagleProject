# SPHEREx Explorer API

One FastAPI app (`backend/api/main.py`) joins the four backend workstreams. Interactive docs with
every parameter and schema are at **`/docs`** (Swagger UI) and **`/openapi.json`** on a running
server. This page lists the endpoints, who owns each one, and the contracts between them.

The contract is set by the API layer rather than by each module, so a change inside one module
does not reach the viewer:

- Every response is JSON with snake_case fields, which the viewer's `frontend/src/types.ts` mirrors
  one to one. `tests/test_integration.py::test_viewer_contract_matches_backend` fails if the two
  drift apart.
- Errors are always `{"detail": "..."}` with a meaningful status:
  - 400: bad input, such as unparseable coordinates, FOV out of range or an unknown option.
  - 404: unknown id or file.
  - 422: schema violation.
  - 429: rate limited; the `Retry-After` header says when to retry.
  - 502: IRSA or CDS failed upstream.
  - 503: busy; heavy jobs are queued.
  - 500: an unexpected error. The message gives a request id, and details go to the server log.
- Every response carries an `X-Request-ID`, which is also written in the log line.

## Positions

Every field endpoint accepts either `q` or `ra` and `dec`:

- **`q`** is a name or a coordinate string. Names are resolved by CDS Sesame, and a catalogue proper
  motion is propagated to the reference epoch.
- **`ra`/`dec`** can be degrees or sexagesimal, for example `ra=08h55m10.831s&dec=-07d14m42.53s`.

`fov` is the field width in degrees. Analysis allows up to 0.5°; search and processing allow up to
`SPHEREX_MAX_FOV_DEG`.

## Endpoints

| Method & path | Owner | Purpose |
|---|---|---|
| `GET /api/health` | P5 | Liveness check |
| `GET /api/version` | P5 | App, processing and analysis versions; whether the viewer is bundled |
| `GET /api/targets` | P1 | The five challenge targets (resolved by name, never hard-coded coordinates) |
| `GET /api/releases` | P1 | Known SPHEREx releases and the default selection (DR1 + QR3) |
| `GET /api/resolve?q=` | P1 | Name or coordinates → position (+ proper motion, parallax) |
| `GET /api/observations?q=&fov=` | P1 | Archive search: every SPHEREx image covering the field, with epochs |
| `POST /api/search` | P1 | Same as above, with the parameters in a JSON body |
| `GET /api/observations/{id}` | P1 | Metadata of one observation (time, detector, wavelength, release, URLs) |
| `GET /api/observations/dates`, `/bands` | P1 | Availability summaries: dates and wavelengths covered |
| `POST /api/cutouts` | P1 | Download FITS cutouts (cached; large requests capped by `SPHEREX_MAX_CUTOUTS_PER_REQUEST`) |
| `GET /api/files/cutouts/{path}` | P1 | Serve a downloaded cutout |
| `GET /api/processing/options`, `/api/channels` | P2 | Mask profiles, stacking options, the 102 nominal channels |
| `POST /api/process` | P2 | Calibrated, masked, background-subtracted frames on one north-up grid (+ optional stacks) |
| `POST /api/compare` | P2 | Difference or ratio of two dates/visits (PSF- and background-matched), plus significance map |
| `GET /api/files/processed/{path}` | P2 | Processed FITS / PNG / GIF products |
| `GET /api/catalog/{gaia,catwise}` | P3 | Catalogue overlay sources with proper motions |
| `GET /api/analysis` | P4 | Detect, track and classify sources in the field (viewer overlays + per-source summaries) |
| `POST /api/analysis/detect` | P4 | The same, with the field in a JSON body |
| `GET /api/analysis/{source_id}` | P4 | Complete record of one source: every detection with time, position and errors |
| `GET /api/light-curve/{source_id}` | P4 | Flux (mJy, AB mag) against time + variability test |
| `GET /api/spectrum/{source_id}` | P4 | Per-visit and combined spectrum, ratio of each visit to the first, `compare_to` another source |
| `GET /api/analysis/crossmatch` | P4 | Gaia DR3 / CatWISE2020 / SIMBAD (/ SkyBoT) look-up of one position |

The prompt's suggested routes all exist. `GET /api/analysis/{source_id}` and
`GET /api/spectrum/{source_id}` take the field as query parameters, because source ids
(`S001`, …) are numbered per field. The field's analysis is cached on disk and in memory, so
these calls are cheap after the first `GET /api/analysis` for that field.

## Key contracts

### `POST /api/process` → `ProcessResponse`

```jsonc
{
  "grid": {"center_ra": .., "center_dec": .., "fov_deg": .., "shape": [h, w], "pixel_scale_arcsec": 6.15,
           "wcs": {...}, "orientation": "north up, east left"},
  "count": 54,
  "observations": [{          // sorted by time
    "observation_id": "2025W38_1A_0344_2_D6", "observation_time": "2025-09-17T03:12:44", "mjd": 60935.13,
    "detector": 6, "wavelength": 4.623, "bandwidth": 0.041, "channel": {"index": 93, "label": ".."},
    "data_release": "DR1", "wavelength_source": "CWAVE/CBAND", "wavelength_calibration": "cal-swcs-v5-2026-191",
    "pipeline_version": "7.0.5", "units": "MJy/sr", "psf_fwhm_arcsec": 8.1, "valid_fraction": 0.98,
    "quality_flags": ["SOURCE"], "fits_url": "/api/files/processed/…fits", "image_url": "/api/files/processed/…png",
    "processing_method": "..", "history": [..]
  }],
  "errors": [], "skipped": [], "query": {"kind": "name", "name": "..", "target": {..}}
}
```

### `GET /api/analysis` → `AnalysisResponse`

```jsonc
{
  "sources": [SourceMarker],        // viewer overlay markers
  "light_curves": [LightCurve],     // up to 4, target first
  "spectra": [Spectrum],            // per visit, for the first light-curve source
  "results_summary": [SourceSummary],
  "summary": {"n_observations": 91, "n_visits": 6, "n_sources": 8, "classes": {..}},
  "target": {"source_id": "S001", "median_offset_from_prediction_arcsec": 0.86},
  "warnings": [".."], "disclaimer": "..", "provenance": {"releases": ["DR1", "QR3"], ..},
  "registration_calibration": {..}, "visits": [..], "catalogues": {..},
  "results": [..]                   // only with full=true: the complete records
}
```

`SourceSummary` fields are listed in `frontend/src/types.ts`:

- identity: `source_id`, `ra`, `dec`, `ref_mjd`;
- class: `classification`, `label`, `confidence`, `status`, `subtype`, `note`;
- flags: `is_target`, `saturated`, `artefact_risk`;
- evidence: `n_visits`, `angular_motion`, `variability_indicator`, `known_catalogue_match`, `criteria`;
- products: `has_light_curve`, `has_spectrum`.

The complete record (`GET /api/analysis/{id}`) is the Prompt-4 output contract
`{source_id, ra, dec, detections[], angular_motion, motion_uncertainty, variability_indicator,
classification, confidence, known_catalogue_match}`, plus criteria, light curve and spectrum.
See `backend/analysis/README.md`.

`status` is always `known` or `candidate`. A candidate is not a discovery; the disclaimer says
so in every response.

## Limits

| Setting (env) | Default | Applies to |
|---|---|---|
| `SPHEREX_RATE_LIMIT_PER_MIN` | 120 | Requests per client per minute to search / observations / resolve / catalog / cutouts / process / compare / analysis routes (0 = off) |
| `SPHEREX_MAX_CONCURRENT_JOBS` | 3 | Process / compare / analysis / cutout requests running at once; the rest wait |
| `SPHEREX_JOB_QUEUE_TIMEOUT_S` | 300 | Wait before a queued job returns 503 |
| `SPHEREX_MAX_FOV_DEG` | 2.0 | Search/process field size (analysis: 0.5°) |
| `SPHEREX_MAX_CUTOUTS_PER_REQUEST` | 400 | Cutouts per request |
| `SPHEREX_HTTP_TIMEOUT_S` / `_RETRIES` | 120 s / 6 | Calls to IRSA/CDS, with exponential back-off |

File routes (`/api/files/...`) and health/version checks are never rate limited.
