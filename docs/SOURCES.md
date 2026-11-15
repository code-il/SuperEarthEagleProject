# Sources, licences and AI disclosure

This project is an independent entry to the NASA International Space Apps Challenge. It is **not**
produced, endorsed or operated by NASA, JPL, Caltech/IPAC or the SPHEREx team, and nothing in it
should be read as implying that. The app shows no NASA or SPHEREx logos (the favicon is our own
drawing); mission names are used only to say where data comes from.

## Content types and how they are marked

| Type | What it is | How it is shown |
| --- | --- | --- |
| **Real observation / catalogue data** | SPHEREx images and spectra, Gaia DR3, SIMBAD, NASA Exoplanet Archive and JPL Horizons values, used as published | "Real data" tag; the source is named next to the value |
| **Derived measurement** | Our own computation from real data (forced photometry spectra, k-NN class predictions, N-body orbit integrations) | Labelled with the method and its stated uncertainty, e.g. "Predicted: Gaia k-NN model", "Orbit prediction · not an observation" |
| **Illustration** | Anything drawn for orientation that is not measured (the faint Milky Way disk seen from far away; orbit orientation around the line of sight, which cannot be measured) | "Illustration" tag, or stated in the legend beside it |
| **Training data** | Citizen-science tasks whose answer is already catalogued | "Training" tag; earns participation XP only |
| **Simulated demonstration data** | Scientific-review records an administrator enters to demonstrate the validated leaderboard | "Simulated" tag, kept in a separate table, never counted as real validation |
| **Community result** | Player submissions and blind reviews | "Community" tag; never presented as scientific validation |

## Data sources

| Source | Used for | Terms |
| --- | --- | --- |
| SPHEREx Level 2 spectral images and all-sky HiPS (DR1, QR), via NASA/IPAC Infrared Science Archive (IRSA) | Time-lapse viewer, spectra, investigation observation lists | NASA data, public. Acknowledge "This publication makes use of data from SPHEREx, a NASA mission managed by JPL/Caltech, obtained from IRSA." |
| Gaia DR3 (ESA/Gaia/DPAC) | Positions, parallaxes, proper motions, photometry for the Atlas and the moving-object checks | CC BY-SA 3.0 IGO; cite Gaia Collaboration 2016, 2023 |
| SIMBAD (CDS, Strasbourg) | Names, object types, spectral types | Free for scientific use; cite Wenger et al. 2000 |
| NASA Exoplanet Archive (pscomppars) | Planet orbits and host-star parameters | Public; acknowledge the NASA Exoplanet Archive, operated by Caltech under contract with NASA |
| JPL Horizons | Solar-system ephemerides used to check the N-body engine | Public (JPL Solar System Dynamics) |
| CatWISE2020 (via IRSA) | Catalogue overlays in the image viewer | Public; cite Marocco et al. 2021 |
| Aladin Lite (CDS) | Sky preview widget | GPL-3.0 |
| Kopparapu et al. 2014 | Habitable-zone fit | Published paper (ApJL 787, L29) |
| Inter, JetBrains Mono (Google Fonts) | Interface typefaces | SIL Open Font License 1.1 |

## Software

Python: FastAPI, NumPy, Astropy, Astroquery, SciPy (BSD/MIT-style licences). JavaScript: React,
React Router, three.js, Tailwind CSS, Vite (MIT). See `requirements.txt` and `frontend/package.json`
for exact versions.

## Use of AI

- Code and documentation in this repository were written with the help of an AI coding assistant
  (Anthropic Claude). Every scientific value shown in the app comes from the data sources above or from
  code that computes it from them, and is covered by tests (`tests/`, `frontend/src/**/*.test.ts`).
- **No AI-generated images** are used anywhere in the app. If any are added later they must carry a
  visible "AI-generated" watermark and be listed here.
- No AI model classifies sources at runtime. The "predicted" classes come from a k-nearest-neighbours
  model trained on SIMBAD spectral types (documented in the Atlas's About panel, with held-out accuracy).

## Citizen-science outcomes

Automated quality checks test that a submission is complete and consistent with real observations.
Community review measures agreement between independent investigators. Neither is scientific
validation. Only an outcome recorded from a named external review organisation, with a reference,
appears as "Scientifically validated". A moving source that matches nothing in JPL Horizons is
**not** a discovery.
