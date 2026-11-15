"""Offline tests for the 3D galaxy map: classes, the k-NN model and the SPHEREx spectral classifier."""
from __future__ import annotations

import numpy as np
import pytest

from backend.galaxy.classes import CLASS_ID, class_from_hr, class_from_simbad, simbad_flags, FLAGS
from backend.galaxy.model import KNNClassifier, train_and_evaluate
from backend.galaxy.spectral import _bnu, classify_spectrum

# SPHEREx-like wavelength grid (0.75-5 um, log spaced, 102 channels).
W = np.geomspace(0.753, 4.988, 102)


def _spectrum(flux, snr=50.0):
    return [{"w": float(w), "f": float(f), "e": float(abs(f) / snr + 1e-6), "n": 3} for w, f in zip(W, flux)]


@pytest.mark.parametrize("otype,sp,expected", [
    ("PM*", "M4.5V", "m"), ("BD*", "", "bd"), ("*", "T8", "bd"), ("*", "L5", "bd"), ("WD*", "", "wd"),
    ("*", "DA2", "wd"), ("*", "G2V", "g"), ("*", "K0III", "giant"), ("TT*", "K7", "yso"), ("*", "B9.5V", "ob"),
    ("*", "A1V", "a"), ("*", "sdM1", "m"),
])
def test_class_from_simbad(otype, sp, expected):
    assert class_from_simbad(otype, sp) == CLASS_ID[expected]


def test_class_from_simbad_unknown():
    assert class_from_simbad("*", "") is None
    assert simbad_flags("EB*") & FLAGS["variable"] and simbad_flags("EB*") & FLAGS["multiple"]


def test_hr_rules():
    assert class_from_hr(0.0, 12.0) == CLASS_ID["wd"]      # blue and faint
    assert class_from_hr(1.2, 0.5) == CLASS_ID["giant"]    # red and luminous
    assert class_from_hr(0.82, 4.7) == CLASS_ID["g"]       # the Sun
    assert class_from_hr(2.8, 11.0) == CLASS_ID["m"]
    assert class_from_hr(4.6, 16.0) == CLASS_ID["bd"]


def test_knn_learns_separable_classes():
    rng = np.random.default_rng(0)
    a = rng.normal([4.7, 0.8, 0.3, 0.5], 0.1, (300, 4))
    b = rng.normal([11.0, 2.8, 1.3, 1.4], 0.1, (300, 4))
    X, y = np.vstack([a, b]), np.array([3] * 300 + [5] * 300)
    model, report = train_and_evaluate(X, y)
    assert report["accuracy"] > 0.98
    p = model.predict_proba(np.array([[4.7, 0.8, 0.3, 0.5], [11.0, 2.8, 1.3, 1.4]]))
    assert p[0].argmax() == 3 and p[1].argmax() == 5
    assert isinstance(model, KNNClassifier)


@pytest.mark.parametrize("T,expected", [(3200, "m"), (4500, "k"), (5600, "g")])
def test_blackbody_temperature_classes(T, expected):
    c = classify_spectrum(_spectrum(_bnu(W, T) / _bnu(W, T).max() * 50))
    assert c["class_id"] == CLASS_ID[expected], c["evidence"]
    assert abs(c["t_color_k"] - T) / T < 0.15


def test_methane_band_makes_a_brown_dwarf():
    f = _bnu(W, 1100) / _bnu(W, 1100).max() * 5
    f = np.where((W > 3.2) & (W < 3.4), f * 0.2, f)  # deep CH4 3.3 um absorption
    c = classify_spectrum(_spectrum(f))
    assert c["class_id"] == CLASS_ID["bd"]
    assert "CH4 3.3 µm" in c["features"]


def test_infrared_excess_flagged():
    f = _bnu(W, 4000) / _bnu(W, 4000).max() * 50
    f = np.where(W > 4.0, f * 3.0, f)
    c = classify_spectrum(_spectrum(f))
    assert "4–5 µm excess" in c["features"]


def test_too_faint():
    c = classify_spectrum(_spectrum(np.full(102, 0.01), snr=0.5))
    assert c["class_id"] is None


@pytest.mark.parametrize("main_id, names, expected", [
    ("NAME Proxima Centauri", ["Proxima", "Proxima Cen", "Proxima Centauri"], "Proxima Centauri"),
    ("* alf CMa", ["Sirius", "Sirius A"], "Sirius"),
    ("* alf CMa B", ["Sirius B", "Sirius BC", "Sirius C"], "Sirius B"),
    ("* alf UMi", ["Lodestar", "North Star", "Polaris"], "Polaris"),
    ("Wolf   28", ["van Maanen 2", "van Maanen Star", "van Maanen's Star", "vMA 2"], "van Maanen's Star"),
    ("LP  791-18", [], "LP 791-18"),
])
def test_display_names(main_id, names, expected):
    from scripts.build_galaxy_map import best_name
    assert best_name(main_id, names) == expected


def test_compact_catalog_roundtrip(tmp_path):
    """The committed catalogue keeps every column (derived ones recomputed) at its stored precision."""
    from backend.galaxy.catalog import load_catalog, save_catalog, tangential_velocity

    n = 3
    xyz = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], np.float32)
    xyz.tofile(tmp_path / "stars.bin")
    plx = np.array([100.0, 768.07, 2.5])
    pmra, pmdec = np.array([10.0, np.nan, -3.0]), np.array([0.0, 5.0, 4.0])
    cols = dict(source_id=np.arange(n, dtype=np.int64), ra=np.array([10.123456789, 217.42894, 0.0]),
                dec=np.array([-5.987654321, -62.67949, 1.0]), plx=plx, pmra=pmra, pmdec=pmdec,
                name=np.array(["", "Proxima Centauri", "Barnard's star"]), flags=np.array([1, 2, 178]),
                dist=1000 / plx, vtan=tangential_velocity(pmra, pmdec, 1000 / plx), xyz=xyz)
    save_catalog(tmp_path / "c.npz", **cols)
    c = load_catalog(tmp_path / "c.npz", tmp_path / "stars.bin")
    assert c["ra"][0] == 10.123456789 and c["dec"][0] == -5.987654321  # full precision kept
    assert list(c["name"]) == ["", "Proxima Centauri", "Barnard's star"]
    assert np.allclose(c["dist"], 1000 / plx, rtol=1e-6)
    assert np.allclose(c["vtan"], cols["vtan"], rtol=1e-6)
    assert np.array_equal(c["xyz"], xyz) and list(c["flags"]) == [1, 2, 178]


def test_committed_catalog_matches_stars_bin():
    """data/galaxy/catalog.npz and frontend/public/galaxy/* ship together and describe the same objects."""
    import json

    from backend.galaxy.catalog import CATALOG_PATH, STARS_BIN, load_catalog

    meta = json.loads((STARS_BIN.parent / "meta.json").read_text(encoding="utf-8"))
    c = load_catalog()
    assert CATALOG_PATH.exists() and len(c["ra"]) == meta["count"]
    assert STARS_BIN.stat().st_size >= meta["count"] * 12
