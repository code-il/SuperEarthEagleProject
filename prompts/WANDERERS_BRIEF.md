# WANDERERS — Project Brief
**NASA Space Apps 2026 · Challenge: "Planet X and SPHEREx" (Advanced) · Hackathon Nov 14–15, 2026**

> *"Planet" comes from the Greek* planētēs *— "wanderer". The ancient Greeks found planets because they moved against the fixed stars. We're doing the same thing with SPHEREx.*

---

## 1. One-liner
A public web app where anyone can fly around the real infrared sky as seen by NASA's SPHEREx, zoom into any patch, **watch it change over a year**, and help find things that move — with AI that pre-screens the sky, identifies each object from its 102-band spectrum, and explains it in plain English.

**Hook for the average person:** *"There could be an undiscovered planet or a cold, failed star a few light-years from the Sun. It's in this data. Nobody has looked yet. You could be the one who finds it."*

**Hook for NASA scientists:** Gaia-predicted motion vectors to separate known from unknown movers, spectral typing from SPHEREx's 0.75–5 µm coverage (methane, water, CO features), an ML detector with injection–recovery completeness numbers, and a pipeline that picks up new weekly data automatically.

---

## 2. What the challenge actually asks for (Relevance — do NOT miss this)
- **Required:** a public web tool. The user enters RA/Dec, and the tool shows a time series of SPHEREx images of that patch "like a little video" so moving objects are easy to spot.
- **Required test targets:** Barnard's Star, Epsilon Indi B, WISE 0855 (RA 08h55m10.831s, Dec −7°14′42.53″), RW Aurigae, R Draconis.
- **Suggested (each one adds points):**
  - easy to update with new data releases
  - creative use of the 102 bands, e.g. stacking per detector array; *spectra on the fly = "extra kudos"*
  - add or subtract images from different epochs
  - user-selectable field of view
  - Gaia/WISE overlays
  - coordinates in the URL so the view can be embedded
  - show contamination flags
  - use Astropy / Aladin

Everything below is built around that list. The "universe map" is the front door; the blink viewer is the core.

---

## 3. The experience (what users see)

### A. The Sky Globe (landing page, the "wow")
- Full-screen **Aladin Lite v3** globe showing the **SPHEREx DR1 all-sky HiPS cube**. You're inside the celestial sphere and can drag and zoom anywhere.
- A **wavelength slider** (0.75 → 5 µm) moves through the 102 channels. The sky visibly changes as you go: dust lanes vanish, ices glow, cool stars brighten. This alone will hold a non-expert's attention.
- Pins for the 5 test targets plus "featured discoveries".
- Search bar in plain English ("show me the coldest brown dwarf", "Barnard's Star", "08 55 10.8 −07 14 42"), handled by Gemini → SIMBAD resolver → coordinates.

### B. Blink Lab (click anywhere / search → zoom in)
- Cutout time series per epoch, played as an animated blink. Controls: play/pause, speed, and FOV slider (0.02°–0.5°).
- **Modes:** single detector band (D1–D6) · per-detector stack (all channels summed → better S/N) · false-colour RGB (D1/D3/D6) · **difference** (epoch N − epoch 1) · epoch co-add.
- **Gaia arrows:** each Gaia DR3 star near the field gets a vector showing where its proper motion predicts it should be on each SPHEREx date. A mover with **no** Gaia arrow is flagged "⚠ Unknown mover".
- Flags toggle: overlay the FLAGS extension (cosmic rays, saturation, persistence, etc.).
- **Click a source → Spectrum panel**: 102-point spectrum (0.75–5 µm), molecular bands labelled, AI classification, and a brightness-over-time plot for variable stars.
- Shareable URL: `/?ra=133.795&dec=-7.245&fov=0.1&mode=diff`

### C. The Hunt (citizen science)
- AI-ranked candidate patches are served one at a time. You answer **Moving / Not moving / Artifact**.
- Consensus (e.g. ≥5 votes, ≥70% agreement) promotes a candidate, and every voter is credited as a co-discoverer.
- Google sign-in, leaderboard, badges ("First Brown Dwarf", "Asteroid Wrangler", "100 Patches").
- Votes become labels that retrain the detector (human-in-the-loop).

### D. Sky Guide (AI explainer, on every object)
- "What is this? How far? How fast? Why the dip at 3.3 µm?"
- Gemini answers **only from tool outputs** (measured motion, Gaia match, spectrum class, catalog lookups), so it can't hallucinate physics. Every claim cites the number it came from.

### E. The Neighbourhood (3D, the finale)
- A **Three.js** 3D map of the Sun's neighbourhood (~20 light-years), built from Gaia DR3 distances, with motion trails showing where each star is drifting.
- Objects confirmed in the app (e.g. WISE 0855, Epsilon Indi Ba/Bb) are placed with their measured distance and velocity.
- This is the "real universe map" moment, and every position in it comes from real measurements.

---

## 4. Real-world physics (put this in the app's "How it works" page and in the pitch)

| Concept | Formula / fact | How we use it |
|---|---|---|
| **Proper motion** | μ (arcsec/yr): an object's drift across the sky | Barnard's Star μ ≈ 10.4″/yr (the fastest known star). SPHEREx pixels are ~6″, so that's ~1.7 px/yr. Needs **sub-pixel centroiding + difference imaging**. |
| **Tangential velocity** | v_t = 4.74 · μ · d (km/s, μ in ″/yr, d in pc) | Shown in the Sky Guide: "moving at X km/s across our view". |
| **Parallax** | d (pc) = 1 / p (″) | Earth moves 2 AU between SPHEREx surveys 6 months apart, so nearby objects wobble. Barnard p ≈ 0.55″, WISE 0855 p ≈ 0.44″. **Be honest:** at ~6″ pixels, SPHEREx-only parallax is marginal. Use Gaia distances where they exist and show the SPHEREx estimate with error bars. |
| **Wien's law** | λ_peak = 2898 µm·K / T | T dwarf (~1000 K) peaks near 2.9 µm, right in SPHEREx's range. WISE 0855 (~285 K, colder than the North Pole) peaks ~10 µm, so it shows up only at the red end (4–5 µm), which is why it's nearly invisible to Gaia. |
| **Molecular fingerprints** | H₂O 1.4, 1.9, 2.7 µm · CH₄ 1.6, 2.2, **3.3 µm** · CO₂ 4.3 µm · CO 4.6 µm | Brown dwarf = deep H₂O + CH₄ absorption. Star = smooth Rayleigh-Jeans tail. Asteroid = reflected sunlight + thermal rise at 4–5 µm. Galaxy/quasar = doesn't move. |
| **Interstellar ices** | H₂O ice 3.0 µm, CO₂ ice 4.27 µm, CO ice 4.67 µm | Optional "ice mode" on the globe using the SPLICES ice catalogue ("glaciers between the stars"). |
| **Linear variable filters** | Each detector has a filter whose wavelength changes across the chip | Explains *why* SPHEREx takes many exposures: as the telescope steps, each source lands on different pixels, so its spectrum is built up piece by piece. This is also why we group images by date per detector. |
| **Asteroid motion** | Tens of ″ per hour | Asteroids move many pixels *within* a single visit. Label known ones with IRSA's Moving Object Search Tool (MOST) so users don't "discover" them twice. |
| **Variable stars** | R Draconis = Mira variable, ~245-day period; RW Aurigae = young T Tauri star | Light curve across epochs in the Spectrum panel. |

---

## 5. Data sources (all verified working on Nov 14, 2026)

| Dataset | Access | Notes |
|---|---|---|
| **SPHEREx Spectral Images (DR1 + QR3)** | IRSA TAP: `https://irsa.ipac.caltech.edu/TAP/sync` (tables `spherex.artifact` ⨝ `spherex.plane`); SIA collections `spherex_dr1`, `spherex_dr1_deep`, `spherex_qr3`, `spherex_qr3_deep` | DR1 released Nov 2026. DR1 + QR3 together form the uniform set. **Tested:** Barnard's Star → 363 images (240 DR1 + 123 QR3), ~60 per detector D1–D6, 4 epochs: Aug–Sep 2025, Sep–Oct 2025, Mar 2026, Aug–Sep 2026. TAP query took ~40 s, so **cache aggressively**. |
| **SPHEREx cutouts** | `https://irsa.ipac.caltech.edu/` + `uri` + `?center=<ra>,<dec>d&size=<deg>` | Returns MEF FITS: `IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE`. Wavelength per pixel via `WCS(header, fobj=hdul, key="W")`. 2040×2040 full frames. Time = `plane.time_bounds_lower` (MJD). |
| **SPHEREx DR1 all-sky HiPS cube** | `https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube` | **CORS works from the browser** (tested). 102 layers, orders 0–5. Powers the Sky Globe directly in Aladin Lite. |
| **Gaia DR3** | Gaia archive TAP or CDS VizieR `I/355/gaiadr3` | Proper motion, parallax, magnitudes → arrows, distances, 3D map. Challenge says use DR3. |
| **WISE / CatWISE2020** | IRSA TAP | Second motion reference; WISE is how WISE 0855 was found. |
| **SIMBAD / Sesame** | CDS name resolver | Turns "Barnard's Star" into coordinates. |
| **IRSA MOST** | `https://irsa.ipac.caltech.edu/applications/MOST` | Known asteroids/comets in a field at a given time. |
| **SPLICES ice catalogue** | `https://irsa.ipac.caltech.edu/data/SPHEREx/SPLICES/` | Optional ice overlay. |
| **Reference tool** | WiseView (`byw.tools/wiseview`) | The WISE-era version of this idea; we're the SPHEREx + spectra + AI successor. |

⚠ **CORS gotcha (tested):** IRSA's **TAP/SIA and cutout endpoints block browser cross-origin requests**. Only the HiPS works client-side. **All SPHEREx image and query traffic must go through our backend.**

---

## 6. AI plan

### 6.0 What AI we actually have access to (checked on the 2026 Global Offers page, Nov 14)
| Offer | AI relevance | Use it? |
|---|---|---|
| **Google Cloud: $300 credit / 90 days** (card for verification, no auto-charge) | Vertex AI → **Gemini** API; also pays for Cloud Run CPU running our own models | ✅ **Primary.** Claim first. |
| Microsoft Azure: $200 / 30 days (phone + card + MS/GitHub account) | Azure OpenAI / AI Foundry | ❌ Skip. A second cloud for no gain. |
| GitHub Copilot Free (2,000 completions/mo, limited chat) | Coding assistant only | ✅ Dev tool (with Claude Code). Disclose. |
| Microsoft Copilot (copilot.com) | Consumer chat, **no API** | ❌ Can't go in the app. |
| Miro AI | Brainstorming only | Optional for planning. |

**Dev tip:** use a Google AI Studio Gemini API key while building (instant, no billing setup) and switch to Vertex AI on the $300 credit for the deployed site. Our own models (6.1–6.2, 6.5–6.6) need **no AI service**: they're PyTorch / scikit-learn on Cloud Run CPU.

**The rule:** every AI feature must make the tool *better at finding or explaining real objects*. If removing it changes nothing, cut it. Judges score Validity, and NASA scientists penalise decorative AI.

---

### 6.1 Mover detector (CNN). Core AI, biggest Validity win. **[MUST]**
- **Job:** score each sky patch for "something moved here", which ranks the Hunt queue and flags candidates in Blink Lab.
- **Input:** per-epoch co-added stamps (32×32 px, ~3′) for one detector band, plus difference stamps (epoch N − epoch 1), stacked as channels. Mask FLAGS pixels, subtract ZODI.
- **Training data (we have no labels, so we make them):**
  - Pull ~2,000 random real cutouts across the sky (cached).
  - **Inject synthetic movers** using each cutout's own **EPSF** extension as the PSF: random proper motion 0.5–60″/yr, random direction, random flux (S/N 3–50), positions shifted per epoch date. Also inject a parallax wobble for the realistic cases.
  - Negatives: untouched cutouts plus **realistic impostors**: variable stars (flux change, no motion), cosmic rays, edge artifacts, flagged pixels.
- **Model:** small CNN (3–4 conv blocks, ~200k params) or ResNet-8. Binary moving/not + regression head for motion vector (Δx, Δy per year). Trains in <30 min on CPU / Colab GPU.
- **Validation (put on a slide):**
  - **Completeness curve:** % of injected movers recovered vs. speed and S/N.
  - **False-positive rate** per square degree.
  - **Real-object recovery:** does it flag Barnard's Star, WISE 0855, Epsilon Indi B? (All are held out from training.)
- **Output to UI:** heat-map overlay on Blink Lab + "AI confidence 0.94, motion ≈ 8.1″/yr toward PA 285°".

### 6.2 Spectral classifier: "what is this thing?" **[MUST]**
- **Job:** 102-point spectrum (0.75–5 µm) → class + confidence + *why*.
- **Classes:** M/L/T/Y brown dwarf · normal star · asteroid/comet · galaxy/quasar · ice-rich YSO · artifact.
- **Stage 1 (physics baseline, ship first):** χ² template fit against
  - blackbodies (Wien: λ_peak = 2898/T µm)
  - brown dwarf templates with H₂O (1.4, 1.9, 2.7 µm) and CH₄ (1.6, 2.2, 3.3 µm) absorption
  - solar-reflectance + thermal-tail asteroid model
  - flat/power-law extragalactic
- **Stage 2 (ML on top):** gradient-boosted trees (LightGBM) on band ratios + Stage-1 χ² values. Training labels come from cross-matching ~5–10k SPHEREx sources with **SIMBAD object types** (BD*, *, AGN, G, Ast via MOST).
- **Explainability:** SHAP / feature importance → highlight the wavelengths that drove the decision on the spectrum plot ("methane dip at 3.3 µm → 61% of decision").
- **Output:** "Likely T/Y brown dwarf (87%). Evidence: deep CH₄ 3.3 µm, H₂O 1.4 µm, very red 4.5 µm excess."

### 6.3 Gemini Sky Guide, grounded with tool calling **[MUST]**
- **Job:** explain any object in plain English for the public. Detailed mode for experts.
- **Model:** Gemini (Flash for speed) on Vertex AI.
- **Grounding:** Gemini can only answer through **function calls to our own API**:
  - `get_motion(ra,dec)`
  - `get_gaia_match(ra,dec)`
  - `get_spectrum_class(ra,dec)`
  - `get_distance(ra,dec)`
  - `simbad_lookup(name|ra,dec)`
  - `most_known_asteroids(ra,dec,mjd)`
- **System prompt rules:** never state a number that didn't come from a tool; cite the tool for each fact; say "unknown" if no data; explain physics with analogies (e.g. "colder than the North Pole" for 285 K).
- **Two voices:** "Explain like I'm curious" (default) vs. "Show me the science" (μ, ϖ, v_t, χ², flags).
- **Output example:** *"This is WISE 0855, the coldest known brown dwarf, about 285 K. It moved 8.1″ this year [get_motion]. Gaia can't see it [get_gaia_match] because it emits almost nothing in visible light. The dip at 3.3 µm is methane [get_spectrum_class]."*

### 6.4 Natural-language search **[MUST, cheap]**
- Search box → Gemini function call → `resolve(name)` / `parse_coords(text)` / `region_search(constellation, object_type)`.
- Handles "Barnard's Star", "08 55 10.8 −07 14 42", "the coldest brown dwarf", "something moving near Orion".
- Falls back to the plain SIMBAD resolver if Gemini fails, so the app never depends on the LLM to work.

### 6.5 Human-in-the-loop active learning **[SHOULD]**
- The detector picks the **most uncertain** candidates (scores 0.4–0.7) for The Hunt, which is classic active learning.
- Each patch needs ≥5 votes; label = majority if ≥70% agree. Weight voters by their accuracy on hidden known-answer "gold" patches (10% of the queue).
- One retrain round before submission → slide: **"Model F1 before vs. after community votes"**.
- Promoted candidates are credited to every voter as co-discoverers.

### 6.6 Anomaly finder: "the weird ones" **[STRETCH, high wow]**
- Autoencoder (or Isolation Forest) trained on normal spectra and motion features. A high reconstruction error means "doesn't look like anything we know".
- A "Weird Sky" feed on the homepage: *"Objects even the AI can't explain."* This is where a genuine discovery would most likely come from.

### 6.7 "Find more like this" similarity search **[STRETCH]**
- Embed every spectrum (autoencoder latent or PCA) → vector index (FAISS, in memory).
- Click WISE 0855 → "20 objects with a similar spectrum that have no catalog ID", which leads straight into The Hunt.

### 6.8 Auto discovery report **[STRETCH]**
- When a candidate is promoted, Gemini drafts a one-page report from tool outputs only: position, epochs, motion ± error, Gaia/WISE/SIMBAD checks, spectrum class, MOST asteroid check, contributor list.
- Downloadable as Markdown/PDF, ready for an astronomer (e.g. Backyard Worlds team) to follow up.

### 6.9 What we will NOT do
- ❌ AI image "enhancement" / super-resolution / denoising GANs. They invent pixels, and scientists will mark it down on Validity.
- ❌ A generic chatbot not wired to our data.
- ❌ AI-generated images of objects presented as real. Any AI art must be watermarked and must not use NASA logos or mission insignia (submission rules).

### 6.10 AI disclosure text (paste into the submission)
> *AI use: (1) A convolutional neural network we trained on SPHEREx difference images with synthetic injected movers ranks candidate moving objects. (2) A template-fit + gradient-boosted classifier we trained on SIMBAD-labelled SPHEREx spectra classifies objects. (3) Google Gemini (Vertex AI) powers natural-language search and an explainer that is restricted to values returned by our own data APIs. (4) Citizen votes retrain the detector (active learning). (5) Claude Code and GitHub Copilot assisted in writing code. No AI-generated imagery is presented as observational data.*

### 6.11 AI build order inside the main plan
| Hours | AI task |
|---|---|
| h4–6 | 6.4 NL search (with SIMBAD fallback) |
| h16–20 | 6.2 Stage 1 template classifier → Stage 2 if time |
| h20–24 | 6.3 Gemini Sky Guide with tool calling |
| h12–24 (in parallel, separate notebook) | 6.1 injection set → train CNN → completeness plot |
| h24–27 | 6.5 Hunt queue from CNN uncertainty + vote retrain |
| leftover | 6.6 → 6.7 → 6.8 |

---

## 7. Architecture (partner tools)
```
GoDaddy domain ──► Firebase Hosting (React + Vite, Aladin Lite v3, Three.js)
                        │  /api/* rewrite (same origin → no CORS)
                        ▼
                  Cloud Run: FastAPI (Python 3.11)
                  astropy · astroquery · numpy · reproject · photutils · torch(cpu)
                        │
     ┌──────────────────┼──────────────────────┬──────────────┐
  IRSA TAP/cutouts   Gaia DR3 / VizieR     SIMBAD / MOST    Vertex AI (Gemini)
                        │
  Cloud Storage (cached PNG frames + FITS + JSON per ra/dec/fov)
  Firestore (candidates, votes, users, leaderboard) · Firebase Auth (Google)
```

### Key API endpoints
- `GET /api/resolve?q=` → {ra, dec, name}
- `GET /api/epochs?ra&dec&fov&mode&band` → [{mjd, date, png_url, flags_url, n_frames}] (cached)
- `GET /api/spectrum?ra&dec` → [{wavelength_um, flux, err, epoch}] + class
- `GET /api/gaia?ra&dec&fov` → stars with pm vectors projected to each epoch
- `GET /api/candidates/next` · `POST /api/vote`
- `POST /api/guide` → Gemini with tools = the endpoints above
- `GET /api/detect?ra&dec&fov` → CNN mover scores + motion vectors (heat-map PNG + JSON)
- `GET /api/classify?ra&dec` → class, confidence, top feature wavelengths
- `GET /api/similar?ra&dec` (stretch) · `GET /api/weird` (stretch)
- `POST /api/search` → Gemini NL → resolve/parse → {ra, dec, fov}

### Model serving
- CNN (`.pt`, TorchScript) + LightGBM (`.txt`) baked into the Cloud Run image, loaded once at startup, CPU inference (<100 ms per stamp).
- Gemini via `google-genai` SDK (`vertexai=True`, region `us-central1`) using the service account; AI Studio key only in local `.env`.
- Training happens in `/ml` notebooks (Colab or local), not on Cloud Run.

### Processing per request
TAP query → group by detector + epoch (gap > 20 days = new epoch) → parallel cutout fetch (ThreadPool, 10 workers, 120 s timeout, retry) → mask FLAGS → subtract ZODI → reproject onto a common WCS grid → median co-add per epoch → asinh stretch → PNG. Pre-warm the cache for the 5 test targets before judging.

---

## 8. Build order (≈30 h left; cut from the bottom)
1. **[MUST, h0–8]** Backend `/epochs` + Blink Lab with play, FOV, difference mode, URL params. Verify on all 5 targets.
2. **[MUST, h8–12]** Sky Globe landing (Aladin + SPHEREx HiPS + wavelength slider) → click into Blink Lab.
3. **[MUST, h12–16]** Gaia arrows + "Unknown mover" flag + flags overlay.
4. **[MUST, h16–20]** Spectrum panel + rule/template classifier.
5. **[SHOULD, h20–24]** Gemini search + Sky Guide. Deploy to Cloud Run/Firebase + domain.
6. **[SHOULD, h24–27]** Hunt: ~100 pre-computed candidate patches, voting, leaderboard.
7. **[STRETCH]** CNN detector with injection–recovery, 3D Neighbourhood, ice mode.
8. **[LAST 3 h — non-negotiable]** 30 s demo video with subtitles, project page, AI disclosure, public repo, README.

---

## 9. Demo script (30 s video; every second counts)
1. (0–5 s) The globe spins; slide the wavelength from 0.75 to 5 µm. *"This is the whole sky as SPHEREx sees it, in 102 colours of infrared."*
2. (5–12 s) Type "the coldest brown dwarf" → fly to WISE 0855 → blink: it crawls.
3. (12–18 s) No Gaia arrow → "⚠ Unknown to Gaia". Spectrum: water + methane dips → "Likely Y-type brown dwarf, ~285 K. Colder than the North Pole."
4. (18–24 s) The Hunt: a user votes "Moving" on a real candidate; the leaderboard updates.
5. (24–30 s) 3D neighbourhood: WISE 0855 drops in 7.4 ly from the Sun. *"Planet X is out there. Help us find it."*

---

## 10. Winning pattern (from 2024 + 2025 Global Winners)
- No "top 3": **10 category awards**. Aim for **Best Use of Science / Data / Technology**; Galactic Impact and Most Inspirational are the backups.
- Judging criteria: **Impact, Creativity, Validity, Relevance, Presentation**.
- Winners share the following:
  - they answer *exactly* the challenge as written
  - they **process** NASA data rather than just displaying it
  - they use AI/ML for real analysis, not decoration (2024 seismic DNN, 2025 exoplanet AI, 2024 HWO exoplanet ML map)
  - there's an interactive visual you can play with (2024 NEO orrery won Best Use of Science, 2025 3D habitat tool, 2024 AI-hosted 3D museum)
  - they serve a clear audience
- **Validity is what separates winners from others:** show error bars, completeness numbers, and Barnard's Star matching Gaia as proof the pipeline is correct.

## 11. Submission checklist
- [ ] Demo: ≤30 s video with English subtitles **or** ≤7 slides, public external link
- [ ] Public project link (live site + GitHub), no login needed to view
- [ ] Describe NASA data used (SPHEREx DR1/QR3, HiPS) + other data (Gaia, WISE, SIMBAD)
- [ ] **AI disclosure:** paste section 6.10; watermark any AI images or video
- [ ] Claim the Google Cloud $300 offer (Global Offers page) **before** deploying; enable Vertex AI, Cloud Run, Firestore, Storage APIs
- [ ] No NASA logos or mission insignia in AI-generated art
- [ ] "Submit for Judging" before **Sun Nov 15, 11:59 pm local** (confirm on your event page)
