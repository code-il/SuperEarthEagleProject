# SPHEREx Explorer

NASA Space Apps 2026, "Planet X and SPHEREx". A public web app for watching the infrared sky change
with NASA's SPHEREx data.

1. Type an object name or coordinates.
2. Every SPHEREx image of that spot is fetched from NASA/IPAC IRSA and put on one sky grid.
3. Play the images as a time-lapse, compare dates with blink or difference images, and read each
   frame's metadata.
4. Run an analysis that finds moving and variable sources, measures their motion, light curves and
   spectra, and checks them against star catalogues.

Candidates are never presented as discoveries.

## Run it

You need Python 3.11+ and, to build the viewer, Node.js 20.19+ (or 22.12+; Vite 8 refuses older versions). No accounts or API keys are needed:
every data service is public.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

(macOS/Linux: `.venv/bin/python …`.) Then the **single startup command**:

```bash
.venv\Scripts\python -m scripts.start
```

It builds the viewer if needed (`npm ci && npm run build` in `frontend/`) and serves the app and
API at **http://localhost:8000** (API docs at `/docs`). Other modes:

- `python -m scripts.start --dev`: hot reload; the viewer runs on :5173 and the API on :8000.
- `python -m scripts.start --check`: verifies the installation.
<<<<<<< HEAD
- `python -m scripts.start --demo`: also fills the leaderboards with sample players (usernames
  `demo_…`, tagged **demo** on every board) so a demonstration looks alive. They have leaderboard
  points, streaks and Daily Survey runs only: no contributions, so no science output or community
  total includes them. Remove them with `python -m scripts.seed_demo --clear`.
=======
>>>>>>> 15015553c6e704fff68626c1145fa538697a9c25

Settings are read from `.env`; see [`.env.example`](.env.example). For Docker, run
`docker compose up --build`.

`requirements.txt` holds tested version ranges for all workstreams, and `requirements-lock.txt` the
exact environment used for validation. The viewer's versions are locked in
`frontend/package-lock.json`.

<<<<<<< HEAD
## Home page (`/`) and design

The home page (hero, search, the CosmIO game card) sets the look of the site: pure black, clear
"liquid glass" panels whose light follows the pointer, neutral greys, white pill buttons and a
hand-lettered accent font. The 3D Atlas and the Explore viewer use the same design through the
`.cosmic` scope in `frontend/src/index.css`, which re-points the shared `.glass`/`.btn`/`.chip`/
`.seg`/`.field` classes and the slate/teal palette; the citizen-science pages keep their own.
The game card shows a live, compact 3D Atlas (`components/AtlasPreview.tsx`): it downloads the
catalogue only when scrolled into view, pauses off-screen, and shares the download with `/map`.

## 3D Atlas (`/map`)
=======
## 3D Atlas (the front page, `/`)
>>>>>>> 15015553c6e704fff68626c1145fa538697a9c25

A zoomable 3D map of the real stellar neighbourhood, from a planetary system (AU) out to the whole
Milky Way (kpc), one continuous camera. Every point sits at its measured Gaia DR3 distance.

- **573k objects**: every Gaia DR3 star within 100 pc (parallax/error ≥ 10, minus 46k faint
  G > 19 sources with RUWE > 1.4, which are mostly spurious parallaxes in crowded Galactic-plane
  fields), a 1-in-3 sample of bright stars to 4 kpc, 4.3k SIMBAD objects Gaia has no parallax for
  (cold brown dwarfs such as WISE 0855, the Luhman 16 binary), and 4.8k planetary systems from the
  NASA Exoplanet Archive drawn to scale with habitable zones. Names use SIMBAD's common names and
  every alias is searchable (Sirius, Polaris, Proxima Centauri, Barnard's star).
- **Classified**, with the source of every class shown: *measured* from the object's SPHEREx
  DR1+QR3 spectrum (blackbody + H₂O/CH₄ band indices), *catalogued* (SIMBAD spectral type), or
  *predicted* by a k-NN model on Gaia photometry (89.7 % held-out accuracy, shown in the app;
  below G = 19, where BP−RP is unreliable, a second model on M_G and G−RP, flagged uncertain).
  SPHEREx replaces a class only when no catalogue gave one or when it sees brown-dwarf molecular
  bands: a 0.75–5 µm colour temperature cannot tell a white dwarf or giant from a dwarf.
- **Filters** by class, classification source, model confidence, distance and flags (planet hosts,
  variables, binaries, halo stars, invisible to Gaia); colour by class, true colour or source.
- **Frontier mode** highlights what is still unknown and ranks sectors of space by how uncharted
  they are. Pick an object and press *Measure SPHEREx spectrum* to classify it from the images.
- Each object card shows the SPHEREx DR1 all-sky image (HiPS cube, wavelength slider) and opens the
  time-lapse viewer (Explore) as a pop-up over the map (`/?view=explore&ra=…&dec=…`; old
  `/explore?…` links redirect there). Images are bicubic-smoothed for display by default; the
  "Smooth pixels" toggle shows the raw 6.15″ pixels.
- Stars are drawn by their apparent brightness from the camera. The Sun keeps a fixed-size marker
  glow so home is always visible, plus a true-size disk when you zoom in close.
- **Tour**: a guided walkthrough (opens on the first visit, and from the *Tour* button).
- The challenge targets page moved to `/targets`.

Build the map data once (≈5 min download, then cached in `cache/galaxy/`), and optionally measure
SPHEREx spectra for a set of nearby objects:

```bash
.venv\Scripts\python -m scripts.build_galaxy_map
```

```bash
.venv\Scripts\python -m scripts.spherex_survey --limit 16
```

Re-run `build_galaxy_map` after the survey to fold the SPHEREx classes into the map.

## N-body orbit engine (proof of concept, `backend/orbits/`)

By default the Atlas animates analytic two-body Kepler orbits. This engine adds planet–planet
gravity, and is checked against JPL Horizons and corrected by measured data. In the viewer, open a
planetary system and choose **Engine: N-body** in the orbit legend. The planets then follow the
engine's positions, loaded in 120-day stretches. When the engine refuses a system, or can't
reproduce the archive, the legend says why and the Kepler orbits stay.

- **Physics:** Newtonian N-body, plus an optional general-relativity (1PN) term for the central
  star. It integrates with fixed-step RK4 (baseline) or adaptive 8th-order DOP853.
- **Solar System:** starts from JPL Horizons (DE441) states. Masses are from DE440, Earth and
  Moon are separate bodies, and Ceres, Vesta, Pallas and Hygiea are included. Every so often
  Horizons can replace the engine's state, so errors can't build up.
- **Exoplanets:** starts from NASA Exoplanet Archive elements and masses. It then corrects each
  planet's phase and orbit size until the simulated transits reproduce the archive's ephemeris
  (T0, P) within 1σ. Some systems can't be done honestly: missing masses, ω not measured, or
  orbits that cross or sit inside the Hill stability limit. Those are refused, with the reason.
  Systems that don't converge (resonant chains such as TRAPPIST-1) are marked "not converged".

Endpoints: `GET /api/orbits/solar/validate?days=3650`, `GET /api/orbits/system/{i}?days=30`
(transit predictions) and `GET /api/orbits/system/{i}/positions` (the viewer's tracks).

## Citizen science (`/science`, `backend/game/`)

Investigations built from real Atlas sources and their real SPHEREx images, automated quality
checks, blind community review, two separate leaderboards (global contribution and scientifically
validated), streaks, achievements and an admin area. All scoring happens on the server. No extra
setup or packages are needed: state is a SQLite file created on first use.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPHEREX_GAME_DB` | `<SPHEREX_CACHE_DIR>/game/game.db` (`./cache/game/game.db`) | Where the game database lives (in Docker: the `/data/cache` volume) |
| `SPHEREX_GAME_ADMINS` | empty | Comma-separated usernames that become administrators when they register |

### Daily Survey (`/science/daily`, `backend/game/daily.py`)

A once-a-day game built to get as many uncharted coordinates classified as possible:

- **Shared anchors.** Every player gets the same few catalogued stars (known class) each UTC day.
  These are the scored puzzle: exact class = green, neighbouring spectral class = yellow.
- **Real sky, spread out.** Mixed in, and indistinguishable, are uncharted sources picked per
  player: least-classified first, sources already handed out today count as taken, and a source
  stops being handed out after `votes_wanted` independent classifications pass QA. When the open
  pool runs low, more of the catalogue is opened automatically.
- **Reveal at the end.** Which targets were scored is shown only after the run, so every target
  gets the same care. Then a spoiler-free share card (🟩🟨⬛) and the day's rank.
- **Keep surveying.** After the run, unlimited extra uncharted targets, with a hidden anchor every
  few to measure accuracy. Each player's anchor accuracy (`reliability`) weights their vote in a
  candidate's `weighted_consensus_label` and is included in the evidence export.
- **Rooms.** Private boards for a class or club, joined by a 6-character code.

Ranking: anchor score, then coordinates surveyed, then finish time. Answers go through the normal
submission pipeline (same QA, points and blind review). Settings live under `daily` in the admin
config. Known limit: the image viewer needs coordinates, so a player could look an anchor up in
SIMBAD; names, magnitudes and distances are hidden so that takes deliberate effort.

Investigations open for submission once IRSA has returned the field's observation list; if the
archive is down the page says so and nothing is assumed. Sources, licences and AI disclosure:
[`docs/SOURCES.md`](docs/SOURCES.md).

## Demonstration

Open the app and pick **WISE 0855** on the home page. It opens by name; the card also links the
challenge's supplied coordinates, RA 08h55m10.831s, Dec −07d14m42.53s.

1. Wait for the observations to load. The green badge shows how many there are and over which dates.
2. Press **Space** to play, and Space again to pause.
3. Choose **Difference** mode, set B to another visit, and read the metadata panel on the right.
4. In **Analysis**, click the ★ target to see its measured position, proper motion, catalogue match
   and light curve/spectrum.
5. **Copy link** shares exactly this view.

Repeat for Barnard's Star, Epsilon Indi B, RW Aurigae and R Draconis. [docs/DEMO.md](docs/DEMO.md)
has scripted scenarios for all five and the results of a live run.

To run the twelve acceptance steps automatically:

```bash
.venv\Scripts\python -m scripts.demo_workflow
```

## Layout

```
backend/
  data_acquisition/   Person 1: archive search (SIA2), FITS cutouts, metadata, calibration files   (README)
  image_processing/   Person 2: masks, background, reprojection, stacks, difference images       (README)
  analysis/           Person 4: detection, motion, light curves, spectra, classification         (README)
  api/                Person 5: the one FastAPI app (routes for every workstream, limits, logging, viewer serving)
  catalogs.py         Gaia DR3 / CatWISE2020 cone searches for overlays
frontend/             Person 3 viewer + Person 5 app shell: Home, Explore, About & help (React + TS + Tailwind) (README)
scripts/start.py                Single startup command (build + serve, --dev, --check)
scripts/demo_workflow.py        Live acceptance workflow (12 steps x 5 targets) -> examples/demo/
scripts/fetch_targets.py        Live data check for the 5 targets (Person 1)
scripts/validate_processing.py  Live validation of processing (Person 2)
scripts/validate_analysis.py    Live validation of the analysis (Person 4) -> examples/analysis/
docs/API.md                     Endpoints and contracts
docs/DEPLOYMENT.md              Hosting, storage, caching, limits, timeouts, logging
docs/DEMO.md                    Demonstration scenarios, live results, known limitations
docs/SCIENTIFIC_REVIEW.md       Cross-component scientific review (units, wavelengths, releases, PSF)
examples/                       Real outputs of the live checks
tests/                          Offline unit + integration tests; tests/live (pytest -m live)
```

The suggested `scientific_analysis/` folder is `backend/analysis/`, and `sample_data/` is
`examples/`, which holds real outputs.

## Tests

```bash
.venv\Scripts\python -m pytest
```

```bash
cd frontend
```

```bash
npm test
```

- **Backend:** 120 offline tests, including `tests/test_integration.py`, which runs the acceptance
  workflow through the API on synthetic frames and checks the frontend/backend data contract.
- **Viewer:** 29 tests.
- **Live checks:** `pytest -m live` and the `scripts/validate_*.py` / `demo_workflow.py` scripts.

## Known limitations

These are in short; see [docs/DEMO.md](docs/DEMO.md#known-limitations) and
[backend/analysis/README.md](backend/analysis/README.md#scientific-limitations) for more.

- **First visit is slow.** The first visit to a field downloads every cutout from IRSA, which takes
  1–3 min. IRSA sometimes returns HTTP 503, so a few frames can fail; they are listed in the
  Data-quality panel. Later visits are cached.
- **Saturation.** Bright targets (Barnard's Star, RW Aur, R Dra) saturate at 4–5 µm in detector D6.
  They have positions but no photometry, and R Dra is not detected at all. Use shorter-wavelength
  detectors for them.
- **Short baseline.** About a year of visits, with 0.3–1″ errors per visit. Motions below ~1″/yr are
  not measurable, and faint fast movers such as WISE 0855 reach only 3–5σ.
- **Colour mixed with time.** Each frame is at a different wavelength, so apparent brightness
  changes mix colour and time.
- **One server process.** The rate limiter, job queue and in-memory caches live in one server
  process (see DEPLOYMENT).

Data: NASA SPHEREx via NASA/IPAC IRSA. Catalogues: ESA Gaia DR3, CatWISE2020, SIMBAD. Names: CDS
Sesame. Asteroids: IMCCE SkyBoT. Sky context: Aladin Lite.
