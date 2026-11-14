"""Analysis engine (Workstream 4) on synthetic data with known answers."""
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.analysis import (calculate_angular_motion, classify_candidate, detect_sources,
                              extract_source_photometry, find_tracklets, link_detections, analyse_observations,
                              match_to_catalogue, predict_position, to_viewer, variability_indicator)
from backend.analysis.detection import mjysr_to_mjy, psf_model, wavelength_at
from backend.analysis.motion import parallax_factors, separation_arcsec
from backend.image_processing.align import make_reference_wcs
from backend.image_processing.pipeline import ProcessedObservation

RA0, DEC0 = 150.0, 10.0
SCALE = 6.15
FWHM = 8.0


def _frame(sources, mjd, seed=0, n=41, noise=0.05, wl=4.5, oid=None, release="DR1"):
    """Synthetic processed frame: point sources [(ra, dec, flux_mjy)] on Gaussian noise."""
    wcs, shape = make_reference_wcs(RA0, DEC0, n * SCALE / 3600, SCALE)
    rng = np.random.default_rng(seed)
    yy, xx = np.indices(shape).astype(float)
    img = rng.normal(0, noise, shape)
    s = FWHM / 2.3548 / SCALE
    conv = mjysr_to_mjy(wcs)
    for ra, dec, f in sources:
        x, y = wcs.world_to_pixel_values(ra, dec)
        img += f / conv * psf_model(xx, yy, float(x), float(y), s)
    wmap = wl + 0.0003 * (xx - shape[1] // 2)  # LVF-like gradient across the frame
    return ProcessedObservation(
        observation_id=oid or f"o{seed}", observation_time=None, mjd=mjd, detector=6, wavelength=wl, bandwidth=0.04,
        data=img, variance=np.full(shape, noise ** 2), mask=np.zeros(shape, np.uint8),
        flags=np.zeros(shape, np.int64), raw=img.copy(), wavelength_map=wmap, wcs=wcs, psf_fwhm=FWHM,
        data_release=release, processing={"wavelength_source": "CWAVE/CBAND"})


def _offset(ra, dec, dx_arcsec, dy_arcsec):
    return ra + dx_arcsec / 3600 / math.cos(math.radians(dec)), dec + dy_arcsec / 3600


# --------------------------------------------------------------------------
# A. detection and photometry
# --------------------------------------------------------------------------
def test_detect_sources_recovers_position_and_flux():
    ra, dec = _offset(RA0, DEC0, 13.0, -7.0)
    errs = []
    for seed in range(8):
        f = _frame([(ra, dec, 5.0)], 60000.0, seed=seed)
        d = detect_sources(f)
        assert len(d) == 1
        s = d[0]
        assert s["flux_mjy"] == pytest.approx(5.0, rel=0.1)
        assert s["snr"] > 20 and s["significance"] > 20 and s["quality_flags"] == []
        sep = float(separation_arcsec(s["ra"], s["dec"], ra, dec))
        errs.append(sep / math.hypot(s["ra_err_arcsec"], s["dec_err_arcsec"]))
    # positional errors are honest: offsets are of order the quoted uncertainty
    assert np.median(errs) < 2.0


def test_wavelength_is_taken_at_the_source_pixel():
    ra, dec = _offset(RA0, DEC0, 60.0, 0.0)  # ~10 px east of centre (x decreases eastwards)
    f = _frame([(ra, dec, 5.0)], 60000.0)
    s = detect_sources(f)[0]
    assert s["frame_wavelength"] == 4.5
    assert s["wavelength"] == pytest.approx(f.wavelength_map[int(round(s["y"])), int(round(s["x"]))])
    assert abs(s["wavelength"] - 4.5) > 0.002
    assert wavelength_at({"wavelength": 1.0}, 3, 3) == 1.0


def test_forced_photometry_and_off_frame():
    ra, dec = _offset(RA0, DEC0, -20.0, 10.0)
    f = _frame([(ra, dec, 3.0)], 60000.0, seed=3)
    on, off = extract_source_photometry(f, [{"ra": ra, "dec": dec}, {"ra": RA0 + 1, "dec": DEC0}])
    assert on["flux_mjy"] == pytest.approx(3.0, rel=0.1)
    assert on["aperture_flux_mjy"] == pytest.approx(3.0, rel=0.25)
    assert off["flux_mjy"] is None and "off_frame" in off["quality_flags"]


# --------------------------------------------------------------------------
# B/C. motion
# --------------------------------------------------------------------------
def test_angular_motion_linear_fit():
    pm = (-800.0, 10360.0)  # mas/yr, Barnard-like
    t = np.array([60000, 60180, 60365, 60545, 60730.0])
    rng = np.random.default_rng(1)
    pos = [predict_position({"ra": RA0, "dec": DEC0, "ref_mjd": 60000, "pm_ra_cosdec_mas_yr": pm[0],
                             "pm_dec_mas_yr": pm[1]}, m) for m in t]
    pos = [_offset(r, d, *rng.normal(0, 0.2, 2)) for r, d in pos]
    m = calculate_angular_motion(pos, t, 0.2)
    assert m["model"] == "linear"
    assert m["pm_ra_cosdec_mas_yr"] == pytest.approx(pm[0], abs=4 * m["pm_ra_cosdec_err_mas_yr"])
    assert m["pm_dec_mas_yr"] == pytest.approx(pm[1], abs=4 * m["pm_dec_err_mas_yr"])
    assert m["motion_sigma"] > 30 and m["pm_snr"] > 30
    assert m["displacement_arcsec"] == pytest.approx(10.39 * 2, rel=0.05)


def test_static_source_is_not_moving_and_systematics_inflate_errors():
    rng = np.random.default_rng(2)
    t = [60000, 60180, 60365, 60545]
    pos = [_offset(RA0, DEC0, *rng.normal(0, 0.3, 2)) for _ in t]
    m = calculate_angular_motion(pos, t, 0.3)
    assert m["motion_p_value"] > 1e-3
    m2 = calculate_angular_motion(pos, t, 0.3, systematic_arcsec=1.0)
    assert m2["pm_err_mas_yr"] > m["pm_err_mas_yr"]


def test_parallax_is_removed_when_given():
    plx = 547.0  # mas (Barnard's Star)
    t = np.linspace(60000, 60730, 9)
    fa, fd = parallax_factors(RA0, DEC0, t)
    pos = [_offset(RA0, DEC0, plx / 1000 * a, plx / 1000 * d) for a, d in zip(fa, fd)]
    without = calculate_angular_motion(pos, t, 0.05)
    with_plx = calculate_angular_motion(pos, t, 0.05, parallax_mas=plx)
    assert without["linear_chi2_red"] > 20  # parallax looks like non-linear motion
    assert with_plx["linear_chi2_red"] < 1e-3 and with_plx["pm_total_mas_yr"] < 5


def test_non_linear_motion_flagged():
    t = np.array([60000, 60100, 60200, 60300, 60400, 60500.0])
    yr = (t - t[0]) / 365.25
    pos = [_offset(RA0, DEC0, 20 * y * y, 3 * y) for y in yr]
    m = calculate_angular_motion(pos, t, 0.1)
    assert m["model"] == "non_linear" and m["nonlinear_p_value"] < 1e-3


def _det(ra, dec, mjd, epoch=0, oid="f", err=0.3, flux=5.0):
    return {"ra": ra, "dec": dec, "mjd": mjd, "epoch": epoch, "observation_id": oid, "ra_err_arcsec": err,
            "dec_err_arcsec": err, "ra_err_total_arcsec": err, "dec_err_total_arcsec": err, "quality_flags": [],
            "quality": 1.0, "flux_mjy": flux, "snr": 20.0}


def test_linking_follows_a_fast_mover_and_a_static_star():
    mover = {"ra": RA0, "dec": DEC0, "ref_mjd": 60000, "pm_ra_cosdec_mas_yr": 0.0, "pm_dec_mas_yr": 10000.0}
    star = _offset(RA0, DEC0, 25.0, 4.0)
    visits = []
    for k, m in enumerate([60000, 60182, 60365, 60547]):
        visits.append({"mjd": m, "detections": [_det(*predict_position(mover, m), m, k), _det(*star, m, k)]})
    tracks = link_detections(visits)
    assert len(tracks) == 2 and all(len(t.points) == 4 for t in tracks)
    fast = max(tracks, key=lambda t: t.motion()["pm_total_mas_yr"])
    assert fast.motion()["pm_dec_mas_yr"] == pytest.approx(10000, rel=0.1)


def test_tracklet_finds_an_asteroid_not_noise():
    rng = np.random.default_rng(5)
    t0 = 60000.0
    ast = [_det(*_offset(RA0, DEC0, 30 * h, -10 * h), t0 + h / 24, oid=f"a{i}") for i, h in enumerate([0, 0.5, 1.0, 1.6])]
    noise = [_det(*_offset(RA0, DEC0, *rng.uniform(-90, 90, 2)), t0 + rng.uniform(0, 0.1), oid=f"n{i}") for i in range(10)]
    tl = find_tracklets(ast + noise)
    assert len(tl) == 1 and tl[0]["n"] == 4
    assert tl[0]["rate_arcsec_h"] == pytest.approx(math.hypot(30, 10), rel=0.05)


# --------------------------------------------------------------------------
# D. variability (spectrum vs time)
# --------------------------------------------------------------------------
def _sed_points(scales, seed=0):
    rng = np.random.default_rng(seed)
    pts = []
    for e, sc in enumerate(scales):
        for wl in np.linspace(4.4 + 0.02 * e, 4.95, 12):  # each visit samples different wavelengths
            f = sc * 10 * (wl / 4.5) ** -3  # steep spectrum
            pts.append({"epoch": e, "mjd": 60000 + 180 * e, "wavelength": wl, "flux": f * (1 + rng.normal(0, 0.01)),
                        "flux_err": 0.01 * f, "data_release": "DR1"})
    return pts


def test_constant_source_with_steep_spectrum_is_not_variable():
    v = variability_indicator(_sed_points([1, 1, 1]))
    assert v["reliable"] and not v["significant"] and v["p_value"] > 1e-3


def test_brightening_is_detected_and_release_mixing_handled():
    v = variability_indicator(_sed_points([1, 1.6, 1]))
    assert v["significant"] and v["amplitude_mag"] == pytest.approx(2.5 * math.log10(1.6), abs=0.03)
    pts = _sed_points([1, 1.6])
    for p in pts:
        if p["epoch"] == 1:
            p["data_release"] = "QR2"
    v2 = variability_indicator(pts)
    assert not v2["significant"] and "calibrated differently" in v2["release_note"]


# --------------------------------------------------------------------------
# F/G. cross-match and classification
# --------------------------------------------------------------------------
def test_match_to_catalogue_propagates_proper_motion():
    cat = [{"id": "Gaia X", "ra": RA0, "dec": DEC0, "ref_mjd": 57388.5, "pm_ra_cosdec_mas_yr": 0.0,
            "pm_dec_mas_yr": 10000.0},
           {"id": "Gaia static", "ra": RA0, "dec": DEC0 + 0.001, "ref_mjd": 57388.5, "pm_ra_cosdec_mas_yr": 0.0,
            "pm_dec_mas_yr": 0.0}]
    mjd = 57388.5 + 365.25 * 10
    ra, dec = predict_position(cat[0], mjd)
    m = match_to_catalogue({"ra": ra, "dec": dec, "mjd": mjd, "pm_ra_cosdec_mas_yr": 0, "pm_dec_mas_yr": 9800,
                            "pm_err_mas_yr": 300, "motion_significant": True}, cat)
    assert m["id"] == "Gaia X" and m["separation_arcsec"] < 0.1 and m["pm_consistent"]
    assert match_to_catalogue({"ra": RA0 + 0.1, "dec": DEC0, "mjd": mjd}, cat) is None


def _hpm(**kw):
    return {"pm_total_mas_yr": 10000, "motion_sigma": 20, "pm_snr": 20, "displacement_arcsec": 10,
            "linear_chi2_red": 1.0, **kw}


def test_classification_criteria():
    c = classify_candidate({"n_visits": 4, "angular_motion": _hpm(), "quality": 1.0})
    assert c["classification"] == "high_proper_motion_candidate" and c["status"] == "candidate"
    assert 0.8 < c["confidence"] <= 1.0
    assert "requires independent verification" in c["note"]
    known = classify_candidate({"n_visits": 4, "angular_motion": _hpm(), "known_catalogue_match":
                                {"id": "Gaia X", "pm_consistent": True, "pm_difference_sigma": 0.5,
                                 "separation_arcsec": 0.2}})
    assert known["status"] == "known" and known["label"] == "known high-proper-motion object"
    weak = classify_candidate({"n_visits": 4, "angular_motion": _hpm(motion_sigma=2.0)})
    assert weak["classification"] == "stationary"
    # a saturated "mover" without any catalogue counterpart is not a candidate
    sat = classify_candidate({"n_visits": 4, "angular_motion": _hpm(), "saturated": True})
    assert sat["classification"] == "insufficient_data"
    # two visits are not enough for a saturated source
    sat2 = classify_candidate({"n_visits": 2, "angular_motion": _hpm(), "saturated": True,
                               "known_catalogue_match": {"id": "G", "separation_arcsec": 1}})
    assert sat2["classification"] != "high_proper_motion_candidate"
    tl = classify_candidate({"n_visits": 1, "tracklet": {"n": 5, "rate_arcsec_h": 30, "rms_arcsec": 0.5}})
    assert tl["classification"] == "possible_solar_system_object"
    for crit in c["tests"]["high_proper_motion_candidate"]["criteria"]:
        assert {"name", "value", "threshold", "score", "weight", "passed"} <= set(crit)


# --------------------------------------------------------------------------
# End to end on a synthetic series
# --------------------------------------------------------------------------
def _series():
    mover0 = _offset(RA0, DEC0, -20.0, -30.0)
    mover = {"ra": mover0[0], "dec": mover0[1], "ref_mjd": 60000, "pm_ra_cosdec_mas_yr": 3000.0, "pm_dec_mas_yr": 8000.0}
    stars = [(*_offset(RA0, DEC0, 40, 30), 8.0), (*_offset(RA0, DEC0, -45, 25), 6.0), (*_offset(RA0, DEC0, 30, -40), 7.0)]
    obs, k = [], 0
    for v, m0 in enumerate([60000, 60182, 60365, 60547]):
        for j in range(6):
            m = m0 + j * 0.3
            obs.append(_frame(stars + [(*predict_position(mover, m), 5.0)], m, seed=k, wl=4.4 + 0.1 * j, oid=f"v{v}f{j}"))
            k += 1
    return obs, mover


def test_end_to_end_finds_the_mover():
    obs, mover = _series()
    res = analyse_observations(obs, ra=RA0, dec=DEC0, fov=41 * SCALE / 3600, catalogs=(), use_skybot=False)
    assert res["summary"]["n_visits"] == 4
    movers = [s for s in res["sources"] if s["classification"] == "high_proper_motion_candidate"]
    assert len(movers) == 1
    m = movers[0]["angular_motion"]
    assert m["pm_ra_cosdec_mas_yr"] == pytest.approx(3000, abs=4 * m["pm_ra_cosdec_err_mas_yr"] + 300)
    assert m["pm_dec_mas_yr"] == pytest.approx(8000, abs=4 * m["pm_dec_err_mas_yr"] + 300)
    assert movers[0]["status"] == "candidate"  # no catalogue checked -> never "known"
    static = [s for s in res["sources"] if s["classification"] == "stationary"]
    assert len(static) == 3
    rec = movers[0]
    for key in ("source_id", "ra", "dec", "detections", "angular_motion", "motion_uncertainty",
                "variability_indicator", "classification", "confidence", "known_catalogue_match"):
        assert key in rec
    d = rec["detections"][0]
    assert {"mjd", "ra", "dec", "ra_err_total_arcsec", "dec_err_total_arcsec"} <= set(d)
    assert res["registration_calibration"]["n_reference_sources"] == 3
    v = to_viewer(res)
    assert {s["id"] for s in v["sources"]} == {s["source_id"] for s in res["sources"]}
    mk = next(s for s in v["sources"] if s["id"] == rec["source_id"])
    assert mk["kind"] == "candidate" and "pm_dec_mas_yr" in mk
    assert v["light_curves"] and v["light_curves"][0]["units"] == "mJy"


def test_analysis_route(monkeypatch):
    from backend.api import analysis_routes
    obs, _ = _series()
    res = analyse_observations(obs, ra=RA0, dec=DEC0, fov=0.07, catalogs=(), use_skybot=False)
    monkeypatch.setattr(analysis_routes, "analyse_field", lambda *a, **k: res)
    from backend.api.main import app
    c = TestClient(app)
    r = c.get("/api/analysis", params={"ra": RA0, "dec": DEC0, "fov": 0.07, "catalogs": "none"}).json()
    assert {"sources", "light_curves", "spectra", "results", "summary", "warnings", "disclaimer"} <= set(r)
    assert c.get("/api/analysis", params={"ra": RA0, "dec": DEC0, "det": "9"}).status_code == 400
    assert c.get("/api/analysis", params={"ra": RA0, "dec": DEC0, "catalogs": "foo"}).status_code == 400
    assert "results" not in c.get("/api/analysis", params={"ra": RA0, "dec": DEC0, "full": False}).json()


# --------------------------------------------------------------------------
# Calibration / provenance guards (review of corrections 3 and 6)
# --------------------------------------------------------------------------
def test_psf_header_fix_rule():
    from backend.image_processing.io import psf_header_fix_needed
    assert psf_header_fix_needed("6.5.5") and psf_header_fix_needed("6.4.0")
    assert not psf_header_fix_needed("6.5.5+psffix1")
    assert not psf_header_fix_needed("6.5.6") and not psf_header_fix_needed("7.0.5")


def test_spectral_wcs_calibration_lookup_and_mismatch_guard(monkeypatch):
    from astropy.io import fits

    from backend.data_acquisition import calibration as cal
    assert cal.calibration_for_version("7.0.5").spectral_wcs_token == "cal-swcs-v5-2026-191"
    assert cal.calibration_for_version("6.5.6").spectral_wcs_token == "cal-wcs-v4-2025-254"
    cw = np.fromfunction(lambda y, x: 4.0 + 1e-4 * x, (cal.DETECTOR_SIZE, cal.DETECTOR_SIZE), dtype=np.float32)
    cb = np.full_like(cw, 0.04)
    monkeypatch.setattr(cal, "spectral_wcs_path", lambda d, c: "fake")
    monkeypatch.setattr(cal, "_load_spectral_wcs", lambda p: (cw, cb, 0.0013))
    h = fits.Header({"CRPIX1A": -99.0, "CRPIX2A": -199.0, "CTYPE1A": "LINEAR", "CTYPE2A": "LINEAR",
                     "CRVAL1A": 0.0, "CRVAL2A": 0.0, "WCSAXESA": 2, "CDELT1A": 1.0, "CDELT2A": 1.0})
    shape = (3, 4)
    ix, iy = cal.detector_pixels(h, shape)
    assert ix[0, 0] == 100 and iy[0, 0] == 200
    good = cw[iy, ix].astype(float) + 0.0003
    wl, bw, info = cal.science_wavelength_maps(h, shape, 6, "7.0.5", good)
    assert info["wavelength_source"] == "CWAVE/CBAND" and np.allclose(wl, cw[iy, ix])
    # a calibration that disagrees with the image's own lookup table is rejected
    assert cal.science_wavelength_maps(h, shape, 6, "7.0.5", good + 0.0067) is None
