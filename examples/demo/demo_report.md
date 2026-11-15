# Demonstration run (2026-11-14)

App 1.0.0 · processing 1.2 · analysis 1.1 · live IRSA data, detector D6.

## Barnard's Star: 12/12 steps passed

| # | Step | Result | Time |
|---|---|---|---|
| 1 | open the website | ✅ HTTP 200 | 0.0 s |
| 2 | search | ✅ NAME Barnard's star: 61 observations | 0.3 s |
| 3 | retrieve observations | ✅ 57 processed frames (1 failed, 3 skipped) | 2.2 s |
| 4 | chronological display | ✅ 2025-08-15 → 2026-08-20, 4 visits | 0.0 s |
| 5 | play the time-lapse | ✅ first/middle/last frame FITS: [200, 200, 200] | 0.0 s |
| 6 | pause on an observation | ✅ 2026W12_2A_0520_2_D6 (PNG 200) | 0.0 s |
| 7 | compare two dates | ✅ visit 4 vs visit 1, Δt 362.149 d | 1.9 s |
| 8 | difference image | ✅ diverging blue-white-red: red = brighter in A, blue = brighter in B | 0.0 s |
| 9 | metadata | ✅ 2026-03-22T08:46:29 · λ 4.858 µm (CWAVE/CBAND) · D6 · DR1 · flags BLOOM,OVERFLOW,SUR_ERROR,TRANSIENT | 0.0 s |
| 10 | run the analysis | ✅ 3 sources, classes {'high_proper_motion_candidate': 1, 'stationary': 2} | 4.6 s |
| 11 | measured position and motion | ✅ S001 at RA 269.44624 Dec 4.76889, 0.53" from ephemeris; µ = (-1467 ± 567, 10567 ± 456) mas/yr, 23.2σ; known high-proper-motion object (0.62); match Gaia DR3 4472832130942575872 | 0.0 s |
| 12 | share through a URL | ✅ http://127.0.0.1:8000/explore?q=Barnard%27s+Star&fov=0.05&obs=2026W12_2A_0520_2_D6&mode=difference | 0.0 s |

Analysis warnings: The series combines releases DR1, QR3. IRSA documents DR1 + QR3 as one uniformly processed set. The target (S001) is saturated at these wavelengths: its position comes from RAW centroids and it has no reliable photometry, light curve or spectrum.

## Epsilon Indi B: 12/12 steps passed

| # | Step | Result | Time |
|---|---|---|---|
| 1 | open the website | ✅ HTTP 200 | 0.0 s |
| 2 | search | ✅ * eps Ind B: 98 observations | 0.5 s |
| 3 | retrieve observations | ✅ 92 processed frames (1 failed, 5 skipped) | 2.9 s |
| 4 | chronological display | ✅ 2025-04-29 → 2026-09-28, 6 visits | 0.0 s |
| 5 | play the time-lapse | ✅ first/middle/last frame FITS: [200, 200, 200] | 0.0 s |
| 6 | pause on an observation | ✅ 2026W18_1A_0444_1_D6 (PNG 200) | 0.0 s |
| 7 | compare two dates | ✅ visit 6 vs visit 1, Δt 511.785 d | 3.0 s |
| 8 | difference image | ✅ diverging blue-white-red: red = brighter in A, blue = brighter in B | 0.0 s |
| 9 | metadata | ✅ 2026-04-29T03:53:51 · λ 4.557 µm (CWAVE/CBAND) · D6 · DR1 · flags none | 0.0 s |
| 10 | run the analysis | ✅ 9 sources, classes {'high_proper_motion_candidate': 1, 'stationary': 3, 'insufficient_data': 5} | 9.4 s |
| 11 | measured position and motion | ✅ S001 at RA 331.09681 Dec -56.80084, 0.948" from ephemeris; µ = (3850 ± 145, -2547 ± 146) mas/yr, 31.7σ; known high-proper-motion object (0.90); match Gaia DR3 6412596012146801152 | 0.0 s |
| 12 | share through a URL | ✅ http://127.0.0.1:8000/explore?q=eps+Ind+B&fov=0.05&obs=2026W18_1A_0444_1_D6&mode=difference | 0.0 s |

Analysis warnings: The series combines releases DR1, QR3. IRSA documents DR1 + QR3 as one uniformly processed set.

## WISE 0855: 12/12 steps passed

| # | Step | Result | Time |
|---|---|---|---|
| 1 | open the website | ✅ HTTP 200 | 0.0 s |
| 2 | search | ✅ 08h55m10.831s -07d14m42.53s: 45 observations | 4.3 s |
| 3 | retrieve observations | ✅ 44 processed frames (1 failed, 0 skipped) | 143.0 s |
| 4 | chronological display | ✅ 2025-05-05 → 2026-05-11, 4 visits | 0.0 s |
| 5 | play the time-lapse | ✅ first/middle/last frame FITS: [200, 200, 200] | 0.0 s |
| 6 | pause on an observation | ✅ 2025W50_2A_0428_1_D6 (PNG 200) | 0.0 s |
| 7 | compare two dates | ✅ visit 4 vs visit 1, Δt 357.184 d | 14.4 s |
| 8 | difference image | ✅ diverging blue-white-red: red = brighter in A, blue = brighter in B | 0.0 s |
| 9 | metadata | ✅ 2025-12-13T00:41:57 · λ 4.731 µm (CWAVE/CBAND) · D6 · DR1 · flags none | 0.0 s |
| 10 | run the analysis | ✅ 26 sources, classes {'high_proper_motion_candidate': 2, 'stationary': 22, 'insufficient_data': 2} | 30.7 s |
| 11 | measured position and motion | ✅ S013 at RA 133.75917 Dec -7.24200, 1.157" from ephemeris; µ = (-6549 ± 1785, 2947 ± 1401) mas/yr, 3.8σ; known high-proper-motion object (0.42); match CWISE J085506.91-071437.5 | 0.0 s |
| 12 | share through a URL | ✅ http://127.0.0.1:8000/explore?ra=08h55m10.831s&dec=-07d14m42.53s&fov=0.1&obs=2025W50_2A_0428_1_D6&mode=difference | 0.0 s |

## RW Aurigae: 12/12 steps passed

| # | Step | Result | Time |
|---|---|---|---|
| 1 | open the website | ✅ HTTP 200 | 0.0 s |
| 2 | search | ✅ V* RW Aur: 56 observations | 0.3 s |
| 3 | retrieve observations | ✅ 54 processed frames (2 failed, 0 skipped) | 1.8 s |
| 4 | chronological display | ✅ 2025-09-11 → 2026-09-16, 3 visits | 0.0 s |
| 5 | play the time-lapse | ✅ first/middle/last frame FITS: [200, 200, 200] | 0.0 s |
| 6 | pause on an observation | ✅ 2026W09_2A_0224_4_D6 (PNG 200) | 0.0 s |
| 7 | compare two dates | ✅ visit 3 vs visit 1, Δt 362.163 d | 1.8 s |
| 8 | difference image | ✅ diverging blue-white-red: red = brighter in A, blue = brighter in B | 0.0 s |
| 9 | metadata | ✅ 2026-02-27T15:32:34 · λ 4.477 µm (CWAVE/CBAND) · D6 · DR1 · flags OVERFLOW | 0.0 s |
| 10 | run the analysis | ✅ 7 sources, classes {'stationary': 6, 'insufficient_data': 1} | 5.4 s |
| 11 | measured position and motion | ✅ S001 at RA 76.95650 Dec 30.40125, 0.413" from ephemeris; µ = (528 ± 885, -78 ± 832) mas/yr, 0.2σ; stationary (0.38); match Gaia DR3 156430817820015232 | 0.0 s |
| 12 | share through a URL | ✅ http://127.0.0.1:8000/explore?q=RW+Aur&fov=0.05&obs=2026W09_2A_0224_4_D6&mode=difference | 0.0 s |

Analysis warnings: Registration could not be measured (too few Gaia or stationary field stars); a 0.5" per-visit floor is assumed in all position errors. The series combines releases DR1, QR3. IRSA documents DR1 + QR3 as one uniformly processed set. The target (S001) is saturated at these wavelengths: its position comes from RAW centroids and it has no reliable photometry, light curve or spectrum.

## R Draconis: 11/12 steps passed

| # | Step | Result | Time |
|---|---|---|---|
| 1 | open the website | ✅ HTTP 200 | 0.0 s |
| 2 | search | ✅ V* R Dra: 97 observations | 0.4 s |
| 3 | retrieve observations | ✅ 90 processed frames (1 failed, 6 skipped) | 2.6 s |
| 4 | chronological display | ✅ 2025-05-16 → 2026-06-12, 3 visits | 0.0 s |
| 5 | play the time-lapse | ✅ first/middle/last frame FITS: [200, 200, 200] | 0.0 s |
| 6 | pause on an observation | ✅ 2026W17_1B_0434_1_D6 (PNG 200) | 0.0 s |
| 7 | compare two dates | ✅ visit 3 vs visit 1, Δt 346.573 d | 2.8 s |
| 8 | difference image | ✅ diverging blue-white-red: red = brighter in A, blue = brighter in B | 0.0 s |
| 9 | metadata | ✅ 2026-04-22T07:56:42 · λ 4.624 µm (CWAVE/CBAND) · D6 · DR1 · flags BLOOM,OVERFLOW,SUR_ERROR,TRANSIENT | 0.0 s |
| 10 | run the analysis | ✅ 12 sources, classes {'stationary': 7, 'insufficient_data': 5} | 15.3 s |
| 11 | measured position and motion | ❌ Target not detected within 20" of its predicted position (faint, saturated or masked). | 0.0 s |
| 12 | share through a URL | ✅ http://127.0.0.1:8000/explore?q=R+Dra&fov=0.08&obs=2026W17_1B_0434_1_D6&mode=difference | 0.0 s |

Analysis warnings: Registration could not be measured (too few Gaia or stationary field stars); a 0.5" per-visit floor is assumed in all position errors. 9 of 12 sources look like saturation artefacts: the field is dominated by a saturated star, so detections here are unreliable. Try shorter-wavelength detectors. The series combines releases DR1, QR3. IRSA documents DR1 + QR3 as one uniformly processed set.
