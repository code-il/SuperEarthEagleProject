# cosmIO

**Watch the infrared sky move, and fly through the real stellar neighbourhood.**

cosmIO is our entry to the NASA Space Apps Challenge 2026, *"Planet X and SPHEREx"*. It is a
public web app built on NASA's SPHEREx all-sky infrared survey, with three parts:

- **Explorer:** every SPHEREx image of a spot on the sky, as a time-lapse.
- **3D Atlas:** 573,000 real stars, brown dwarfs and planetary systems at their measured distances.
- **Citizen science:** people investigate real sources, and others review them blind.

Everything shown comes from real observations, or is computed from them; anything that isn't is
labelled. Candidates are never presented as discoveries.

![Explorer time-lapse of a SPHEREx field](frontend/docs/screenshots/01-single-timelapse.jpg)

| Side by side: Aug 2025 vs Mar 2026 | Difference image: Barnard's Star moving north |
| --- | --- |
| ![](frontend/docs/screenshots/02-side-by-side.jpg) | ![](frontend/docs/screenshots/03-difference.jpg) |
| **Per-visit median stacks** | **WISE 0855 with its light curve** |
| ![](frontend/docs/screenshots/04-stacked.jpg) | ![](frontend/docs/screenshots/05-wise0855-lightcurve.jpg) |

*All screenshots use real SPHEREx DR1/QR3 data from NASA/IPAC IRSA: detector D6 (4.4–5.0 µm), 3′ field.*

---

## What it does

### Explorer: the infrared sky as a time-lapse

1. **Search:** type a name (`Barnard's Star`, `WISE 0855`) or coordinates.
2. **Fetch:** every SPHEREx spectral image of that spot is found through IRSA's image search, then
   cut out, masked and background-subtracted.
3. **Align:** all the frames are reprojected onto one sky grid.
4. **Compare:**
   - play the frames in time order;
   - *blink* between two dates, or show them *side by side*;
   - take PSF-matched *difference* or *ratio* images;
   - make median or mean *stacks*.
5. **Analyse:**
   - detect sources and measure their proper motion with errors;
   - build light curves and 0.75–5 µm spectra;
   - cross-match against Gaia DR3, CatWISE2020 and known asteroids.

### 3D Atlas: the real neighbourhood in 3D

- **Positions:** each star sits at its measured position. Its direction comes from Gaia DR3 and
  its distance from its parallax, and the two are converted to Galactic x, y, z in parsecs.
- **Contents:**
  - every Gaia DR3 star within 100 pc with parallax/error ≥ 10 (329,824 objects);
  - a sample of bright stars out to 4 kpc (243,022);
  - 4,332 SIMBAD objects that Gaia has no parallax for;
  - 6,470 planets in 4,862 systems, drawn to scale with habitable zones.
- **Classes, each labelled with its source:**
  - *measured* from a SPHEREx spectrum;
  - *catalogued* from SIMBAD;
  - *predicted* by a **k-nearest-neighbour machine-learning model** on Gaia photometry, with
    89.7% accuracy on held-out stars.
- **Orbits:** Kepler orbits by default. An optional **N-body engine** is checked against JPL Horizons.
- **Frontier mode** ranks regions of space by how much in them is still unclassified.

### Citizen science (`/science`)

- **Investigations** are built from real Atlas sources and their real SPHEREx images.
- **Checks:** automated quality checks, then blind community review.
- **Rewards:** streaks, achievements and two separate leaderboards: participation, and
  scientifically validated results.
- **Scoring** happens on the server.

---

## Run it (Windows, macOS, Linux)

You need **Python 3.11+** and **Node.js 20.19+** (or 22.12+). No accounts or API keys are needed,
because every data service used is public.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

(On macOS/Linux, use `.venv/bin/python` in place of `.venv\Scripts\python`.)

```bash
.venv\Scripts\python -m scripts.start
```

This builds the viewer if needed and serves everything at **http://localhost:8000**. The API docs
are at `/docs`. Other modes:

- `--dev`: hot reload.
- `--check`: verifies the installation.

To run it in Docker instead:

```bash
docker compose up --build
```

The Atlas data ships with the repository. To rebuild it from the live catalogues (about 5 minutes):

```bash
.venv\Scripts\python -m scripts.build_galaxy_map
```

**Tests:** `.venv\Scripts\python -m pytest` runs 215 backend tests, and `cd frontend` then
`npm test` runs 49 viewer tests.

### Known limitations

- **The first visit to a field is slow.** It downloads every cutout from IRSA, which takes 1–3
  minutes. If IRSA is down (HTTP 502/503), the Explorer says so.
- **About a year of SPHEREx data so far.** Motions below about 1″/yr cannot be measured yet.
- **Colour is mixed with time.** Each frame is taken at a different wavelength.
- **Positions are a snapshot.** The Atlas shows a 2016 snapshot of positions, and the Milky Way
  disk seen from far out is an illustration.

---

## Project layout

```
backend/
  data_acquisition/   SPHEREx archive search (SIA2), FITS cutouts, metadata
  image_processing/   masks, background, reprojection, stacks, difference images
  analysis/           detection, motion, light curves, spectra, classification
  galaxy/             Atlas catalogue, k-NN classifier, SPHEREx spectral classes
  orbits/             N-body engine checked against JPL Horizons
  game/               citizen-science engine
  api/                FastAPI app
frontend/             React + TypeScript + Tailwind + three.js viewer
scripts/              start, build_galaxy_map, demo and validation scripts
tests/                offline unit and integration tests
```

---

## References

### Data

- [NASA SPHEREx](https://www.jpl.nasa.gov/missions/spherex/): Level 2 spectral images and all-sky
  HiPS (DR1, QR3), via [NASA/IPAC IRSA](https://irsa.ipac.caltech.edu/Missions/spherex.html).
  *"This publication makes use of data from SPHEREx, a NASA mission managed by JPL/Caltech,
  obtained from IRSA."*
- [NASA Exoplanet Archive](https://exoplanetarchive.ipac.caltech.edu/) (pscomppars)
- [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/)
- [ESA Gaia DR3](https://www.cosmos.esa.int/web/gaia/dr3): Gaia Collaboration 2016, 2023 (CC BY-SA 3.0 IGO)
- [SIMBAD](https://simbad.cds.unistra.fr/) (CDS): Wenger et al. 2000
- [CatWISE2020](https://irsa.ipac.caltech.edu/data/WISE/CatWISE/overview.html): Marocco et al. 2021
- [CDS Sesame](https://cds.unistra.fr/cgi-bin/Sesame) name resolver
- [IMCCE SkyBoT](https://ssp.imcce.fr/webservices/skybot/) asteroid service
- [AllWISE](https://wise2.ipac.caltech.edu/docs/release/allwise/), [2MASS](https://irsa.ipac.caltech.edu/Missions/2mass.html), [DSS2](https://archive.stsci.edu/cgi-bin/dss_form) (via Aladin Lite)
- [Kopparapu et al. 2014, ApJL 787, L29](https://doi.org/10.1088/2041-8205/787/2/L29): habitable zones

### Software

- **Python:** [Astropy](https://www.astropy.org/), [Astroquery](https://astroquery.readthedocs.io/), [PyVO](https://pyvo.readthedocs.io/), [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), [reproject](https://reproject.readthedocs.io/), [photutils](https://photutils.readthedocs.io/), [Pillow](https://python-pillow.org/), [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [Pydantic](https://docs.pydantic.dev/), [Requests](https://requests.readthedocs.io/), [pytest](https://docs.pytest.org/), [HTTPX](https://www.python-httpx.org/)
- **JavaScript:** [React](https://react.dev/), [React Router](https://reactrouter.com/), [three.js](https://threejs.org/), [Plotly.js](https://plotly.com/javascript/), [Aladin Lite](https://aladin.cds.unistra.fr/AladinLite/), [Tailwind CSS](https://tailwindcss.com/), [Vite](https://vite.dev/), [TypeScript](https://www.typescriptlang.org/), [Vitest](https://vitest.dev/)
- **Fonts:** [Inter](https://fonts.google.com/specimen/Inter), [JetBrains Mono](https://fonts.google.com/specimen/JetBrains+Mono)

The full list of sources and licences is in [docs/SOURCES.md](docs/SOURCES.md).

---

## Use of AI and branding

- **AI assistance:** code and documentation were written with help from an AI coding assistant
  (Anthropic Claude).
- **No AI images:** no AI-generated images are used.
- **No AI at runtime:** no AI model classifies sources while the app runs. The only trained model
  is the k-NN classifier described above.
- **Not NASA:** cosmIO is an independent project. It is not produced or endorsed by NASA, JPL,
  Caltech/IPAC or the SPHEREx team, and it uses no NASA logos.
