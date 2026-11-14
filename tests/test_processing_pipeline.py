import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient

from backend.data_acquisition import archive, download
from backend.data_acquisition.archive import row_to_record
from backend.data_acquisition.config import Release
from backend.image_processing import (compare, load_processed_observation, make_reference_wcs,
                                      make_stacks, match_wavelengths, process_observation,
                                      process_series, save_processed_observation, select_observations)
from backend.image_processing import pipeline
from backend.image_processing.align import centroid
from backend.image_processing.pipeline import (MASK_INTERP_EDGE, assign_epochs, group_observations,
                                               processed_root)
from backend.image_processing.prepare import MASK_NO_COVERAGE
from tests.conftest import sia_row
from tests.processing_fixtures import make_frame, write_frame

RA, DEC = 150.0, 10.0
MOVE = 6.15 * 3 / 3600  # star moves 3 px north between epochs


def _frames(tmp_path):
    """Three frames: two at epoch 0 (rotations 0 and 120 deg), one at epoch 1 (rotation -60 deg).

    A stationary star sits 0.01 deg east; a mover moves north by 3 px between epochs.
    """
    specs = [
        dict(rot_deg=0, mjd=60900.0, date_obs="2025-08-13T00:00:00", obs_id="E0A", seed=1, mover_dec=DEC),
        dict(rot_deg=120, mjd=60901.0, date_obs="2025-08-14T00:00:00", obs_id="E0B", seed=2, mover_dec=DEC),
        dict(rot_deg=-60, mjd=61080.0, date_obs="2026-02-09T00:00:00", obs_id="E1A", seed=3, mover_dec=DEC + MOVE),
    ]
    paths = []
    for s in specs:
        mover_dec = s.pop("mover_dec")
        stars = [(RA + 0.01, DEC, 4.0), (RA, mover_dec, 4.0)]
        paths.append((s["obs_id"], s["date_obs"], write_frame(tmp_path / f"{s['obs_id']}.fits", stars=stars,
                                                              n=51, noise=0.003, **s)))
    return paths


@pytest.fixture
def processed(tmp_path):
    ref, shape = make_reference_wcs(RA, DEC, 0.06)
    out = []
    for oid, t, p in _frames(tmp_path):
        po = process_observation(p, ref, shape, observation_id=f"{oid}_D6", observation_time=t + "Z")
        po.wavelength = 4.70 if oid != "E1A" else 4.71
        out.append(po)
    return out


def test_process_observation_aligns_and_masks(processed):
    ref = processed[0].wcs
    sx, sy = ref.world_to_pixel_values(RA + 0.01, DEC)
    for p in processed:
        c = centroid(p.data, float(sx), float(sy), 3)
        assert np.hypot(c[0] - sx, c[1] - sy) < 0.15  # stationary star aligned in every frame
        assert np.isnan(p.data[p.mask != 0]).all()
        assert p.processing["mask_profile"] == "standard"
        assert p.background_level is not None
    assert processed[0].observation_id == "E0A_D6" and processed[0].detector == 6


def test_save_and_load_roundtrip(processed, tmp_path):
    p = processed[0]
    path = save_processed_observation(p, {"SRCURL": "x"}, tmp_path / "out" / "p.fits")
    with fits.open(path) as h:
        names = [x.name for x in h]
        assert names == ["PRIMARY", "SCI", "VAR", "MASK", "FLAGS", "RAW"]
        assert h["SCI"].header["CTYPE1"] == "RA---TAN" and h["MASK"].header["MASKB1"] == "NO_COVERAGE"
        assert h["FLAGS"].header["MSKN0000"] == "TRANSIENT"
        assert h[0].header["OBS_ID"] == "E0A_D6" and h[0].header["MASKPROF"] == "standard"
        assert any("ZODI" in str(x) for x in h[0].header["HISTORY"])
    q = load_processed_observation(path)
    assert np.array_equal(q.data.astype(np.float32), p.data.astype(np.float32), equal_nan=True)
    assert np.array_equal(q.mask, p.mask) and q.observation_time == p.observation_time
    assert q.flag_definitions.keys() == p.flag_definitions.keys()


def test_epochs_groups_and_selection(processed):
    ep = assign_epochs(processed)
    assert ep == {"E0A_D6": 0, "E0B_D6": 0, "E1A_D6": 1}
    g = group_observations(processed, "epoch")
    assert list(g) == ["epoch0_D6", "epoch1_D6"] and len(g["epoch0_D6"]) == 2
    assert list(group_observations(processed, "detector")) == ["D6"]
    assert len(group_observations(processed, "channel")) >= 1
    with pytest.raises(ValueError):
        group_observations(processed, "planet")
    assert [o.observation_id for o in select_observations(processed, epoch_index=1)] == ["E1A_D6"]
    assert len(select_observations(processed, date_end="2025-12-31")) == 2
    assert len(select_observations(processed, observation_ids=["E0B_D6"])) == 1
    assert len(select_observations(processed, wavelength_min=4.705)) == 1


def test_difference_shows_motion_dipole(processed, tmp_path):
    a = select_observations(processed, epoch_index=1)
    b = select_observations(processed, epoch_index=0)
    out = compare(a, b, "difference", output_dir=tmp_path / "cmp", name="t")
    with fits.open(tmp_path / "cmp" / "t.fits") as h:
        d = h["DIFF"].data.astype(float)
        sig = h["SIG"].data.astype(float)
        w = make_reference_wcs(RA, DEC, 0.06)[0]
    x_old, y_old = w.world_to_pixel_values(RA, DEC)
    x_new, y_new = w.world_to_pixel_values(RA, DEC + MOVE)
    assert d[int(round(float(y_new))), int(round(float(x_new)))] > 1.0    # brighter in A at new position
    assert d[int(round(float(y_old))), int(round(float(x_old)))] < -1.0   # brighter in B at old position
    # stationary star: residual is a modest fraction of its peak (undersampled PSF => not zero);
    # the mover's residual is close to its full peak
    xs, ys = w.world_to_pixel_values(RA + 0.01, DEC)
    sx, sy = int(round(float(xs))), int(round(float(ys)))
    peak = np.nanmax(processed[2].data[sy - 1:sy + 2, sx - 1:sx + 2])
    stat_res = np.nanmax(np.abs(d[sy - 2:sy + 3, sx - 2:sx + 3])) / peak
    nx_, ny_ = int(round(float(x_new))), int(round(float(y_new)))
    mov_res = np.nanmax(d[ny_ - 1:ny_ + 2, nx_ - 1:nx_ + 2]) / peak
    assert stat_res < 0.45 and mov_res > 0.6 and mov_res > 2 * stat_res
    assert np.isfinite(sig).any()
    assert any("undersampled" in c for c in out["caveats"])
    smooth = compare(a, b, "difference", smooth_fwhm_arcsec=15, output_dir=tmp_path / "cmp", name="t2")
    assert any("smoothed" in h for h in smooth["history"])
    assert out["stats"]["delta_t_days"] > 170
    assert out["image_url"] is None  # outside the processed root -> no URL
    assert any("not a detected moving object" in c for c in out["caveats"])
    assert (tmp_path / "cmp" / "t.png").exists() and (tmp_path / "cmp" / "t_sig.png").exists()


def test_ratio_and_guards(processed, tmp_path):
    a = select_observations(processed, epoch_index=1)
    b = select_observations(processed, epoch_index=0)
    out = compare(a, b, "ratio", output_dir=tmp_path, name="r")
    with fits.open(tmp_path / "r.fits") as h:
        r = h["RATIO"].data.astype(float)
    w = processed[0].wcs
    xs, ys = w.world_to_pixel_values(RA + 0.01, DEC)
    assert r[int(round(float(ys))), int(round(float(xs)))] == pytest.approx(1.0, abs=0.15)  # same brightness
    assert np.isnan(r[0, 0])  # sky: B not significant -> undefined
    with pytest.raises(ValueError):
        compare([], b)
    with pytest.raises(ValueError):
        compare(a, b, "sum")
    other = processed[0]
    other.detector = 3
    with pytest.raises(ValueError, match="different detectors"):
        compare([other], a)


def test_wavelength_matching_and_caveat(processed, tmp_path):
    a = select_observations(processed, epoch_index=1)
    b = select_observations(processed, epoch_index=0)
    b[0].wavelength = 4.95  # far from A (4.71)
    _, _, pairs = match_wavelengths(a, b)
    assert pairs and pairs[0][1] == "E0B_D6"  # picks the closer one (4.70)
    out = compare(a, b[:1], output_dir=tmp_path, name="w")
    assert any("different wavelengths" in c for c in out["caveats"])
    with pytest.raises(ValueError, match="wavelength-matched"):
        compare(a, b[:1], match_wavelength=True, output_dir=tmp_path, name="w2")


def test_make_stacks(processed, tmp_path):
    products = make_stacks(processed, "epoch", "median", output_dir=tmp_path / "st")
    assert [p["key"] for p in products] == ["epoch0_D6", "epoch1_D6"]
    assert products[0]["n_frames"] == 2 and products[0]["fits_url"] is None
    with fits.open(tmp_path / "st" / "stack_epoch_median_epoch0_D6.fits") as h:
        assert h[0].header["NFRAMES"] == 2 and "NFRAMES" in [x.name for x in h]
    processed[0].wavelength = 4.45
    processed[1].wavelength = 4.95
    p2 = make_stacks(processed[:2], "detector", output_dir=tmp_path / "st2")
    assert any("more than one" in w for w in p2[0]["warnings"])
    with pytest.raises(ValueError):
        make_stacks(processed, "epoch", "max")


# ------------------------------------------------------------ series + API (network mocked)
@pytest.fixture
def mocked_archive(monkeypatch, tmp_path):
    """SIA2 returns 3 D6 images; 'downloads' write synthetic frames (rotated, with a mover)."""
    frames = {f"SYN{i}": make_frame(ra=RA, dec=DEC, n=41, rot_deg=rot, mjd=mjd, obs_id=f"SYN{i}",
                                    date_obs=d, seed=i, noise=0.003,
                                    stars=[(RA + 0.01, DEC, 4.0), (RA, DEC + dd, 4.0)])
              for i, (rot, mjd, d, dd) in enumerate([(0, 60900.0, "2025-08-13T00:00:00", 0),
                                                     (90, 60901.0, "2025-08-14T00:00:00", 0),
                                                     (-45, 61080.0, "2026-02-09T00:00:00", MOVE)])}
    rows = [sia_row(f"SYN{i}", 6, mjd, s_region=f"POLYGON ICRS {RA-1} {DEC-1} {RA+1} {DEC-1} {RA+1} {DEC+1} {RA-1} {DEC+1}")
            for i, mjd in enumerate([60900.0, 60901.0, 61080.0])]
    monkeypatch.setattr(archive, "_sia_query", lambda c, *a: [dict(r) for r in rows] if c == "spherex_dr1" else [])
    calls = []

    def fake_fetch(url, dest, progress=None):
        calls.append(url)
        key = next(k for k in frames if f"/{k}D6" in url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(frames[key])
        return len(frames[key]), f"sha-{key}"

    monkeypatch.setattr(download, "_fetch_to_file", fake_fetch)
    return calls


def test_process_series_contract_and_cache(mocked_archive, monkeypatch):
    res = process_series(RA, DEC, 0.04, detectors=[6])
    assert len(res.products) == 3 and not res.errors
    p = res.products[0]
    for k in ("observation_id", "observation_time", "wavelength", "image_url", "fits_url", "wcs",
              "quality_flags", "processing_method"):
        assert k in p
    assert p["image_url"].startswith("/api/files/processed/") and p["fits_url"].endswith(".fits")
    assert p["wcs"]["CTYPE1"] == "RA---TAN"
    assert res.display_limits["D6"][0] < res.display_limits["D6"][1]
    assert res.animations["D6"].endswith(".gif")
    # second run: no re-download, no re-processing
    n_calls = len(mocked_archive)
    monkeypatch.setattr(pipeline, "process_observation", lambda *a, **k: pytest.fail("should use cache"))
    res2 = process_series(RA, DEC, 0.04, detectors=[6])
    assert len(res2.products) == 3 and len(mocked_archive) == n_calls


def test_processing_api(mocked_archive):
    from backend.api.main import app

    c = TestClient(app)
    assert "standard" in c.get("/api/processing/options").json()["mask_profiles"]
    assert len(c.get("/api/channels").json()["channels"]) == 102
    j = c.post("/api/process", json={"ra": RA, "dec": DEC, "fov": 0.04, "detectors": [6],
                                     "stack_by": "epoch"}).json()
    assert j["count"] == 3 and len(j["stacks"]) == 2 and j["grid"]["orientation"] == "north up, east left"
    png = c.get(j["observations"][0]["image_url"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert c.get(j["animations"]["D6"]).status_code == 200
    assert c.get(j["stacks"][0]["fits_url"]).content[:6] == b"SIMPLE"
    cmp_ = c.post("/api/compare", json={"ra": RA, "dec": DEC, "fov": 0.04, "detectors": [6],
                                        "a": {"epoch_index": 1}, "b": {"epoch_index": 0}}).json()
    assert cmp_["mode"] == "difference" and cmp_["a"]["n_frames"] == 1 and cmp_["b"]["n_frames"] == 2
    assert c.get(cmp_["significance_url"]).status_code == 200
    bad = c.post("/api/compare", json={"ra": RA, "dec": DEC, "fov": 0.04, "a": {"epoch_index": 7},
                                       "b": {"epoch_index": 0}})
    assert bad.status_code == 400
    assert c.post("/api/process", json={"ra": RA, "dec": DEC, "mask_profile": "x"}).status_code == 400
    assert c.post("/api/process", json={"ra": RA, "dec": DEC, "stack_by": "x"}).status_code == 400
    assert c.get("/api/files/processed/../../x.png").status_code == 404
