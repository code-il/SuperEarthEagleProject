import json

import pytest

from backend.data_acquisition import archive
from backend.data_acquisition.archive import (get_available_bands, get_available_observation_dates,
                                              get_observation_metadata, search_observations)
from backend.data_acquisition.config import Release, Settings
from backend.data_acquisition.coords import CoordinateError
from tests.conftest import sia_row


@pytest.fixture
def fake_sia(monkeypatch):
    """Replace the network SIA2 call with canned rows per collection."""
    data = {
        "spherex_dr1": [
            sia_row("2025W33_A", 3, 60902.86),
            sia_row("2025W33_A", 6, 60902.86, em_min=4.41e-6, em_max=5.01e-6),
            sia_row("2026W10_B", 3, 61110.10),
            sia_row("2025W33_A", 4, 60902.86, dataproduct_subtype="calibration"),
        ],
        "spherex_qr3": [
            sia_row("2026W33_C", 3, 61268.63, collection="spherex_qr3"),
            # same exposure also in QR3 -> DR1 must win
            sia_row("2025W33_A", 3, 60902.86, collection="spherex_qr3"),
        ],
    }
    calls = []

    def fake(collection, coord, radius, time_range, wl_range):
        calls.append((collection, radius, time_range, wl_range))
        return [dict(r) for r in data.get(collection, [])]

    monkeypatch.setattr(archive, "_sia_query", fake)
    return calls


def test_search_sorted_deduplicated_and_typed(fake_sia):
    recs = search_observations(269.45, 4.69, 0.1)
    ids = [r.observation_id for r in recs]
    assert ids == ["2025W33_A_D3", "2025W33_A_D6", "2026W10_B_D3", "2026W33_C_D3"]
    first = recs[0]
    assert first.data_release == "DR1"  # priority over QR3 duplicate
    assert first.detector == 3 and first.band == "SPHEREx-D3"
    assert first.wavelength_min == pytest.approx(1.6301)
    assert first.observation_time.endswith("Z") and first.observation_time.startswith("2025-08-15")
    assert first.cloud_access["aws"]["bucket_name"] == "nasa-irsa-spherex"
    assert first.quality_flags == [] and first.local_path is None
    assert recs[-1].data_release == "QR3"
    # radius = fov / 2, both default releases queried
    assert sorted(c[0] for c in fake_sia) == ["spherex_dr1", "spherex_qr3"]
    assert all(c[1] == pytest.approx(0.05) for c in fake_sia)


def test_search_contract_fields(fake_sia):
    d = search_observations(269.45, 4.69, 0.1)[0].to_dict()
    for key in ["observation_id", "observation_time", "ra", "dec", "wavelength_min", "wavelength_max",
                "band", "detector", "data_release", "calibration_level", "quality_flags", "fits_url",
                "local_path"]:
        assert key in d
    json.dumps(d)  # serialisable


def test_search_filters(fake_sia):
    assert {r.detector for r in search_observations(269.45, 4.69, 0.1, detectors=[6])} == {6}
    recs = search_observations(269.45, 4.69, 0.1, release="QR3")
    assert {r.data_release for r in recs} == {"QR3"}
    search_observations(269.45, 4.69, 0.1, date_range=("2025-01-01", "2025-12-31"),
                        wavelength_range=(1.0, 2.0))
    _, _, tr, wl = fake_sia[-1]
    assert tr is not None and wl == (1.0, 2.0)
    with pytest.raises(CoordinateError):
        search_observations(269.45, 4.69, 0.1, detectors=[7])
    with pytest.raises(ValueError):
        search_observations(269.45, 4.69, 0.1, release="DR9")


def test_query_cache(fake_sia):
    search_observations(269.45, 4.69, 0.1)
    n = len(fake_sia)
    search_observations(269.45, 4.69, 0.1)
    assert len(fake_sia) == n  # served from disk cache
    search_observations(269.45, 4.69, 0.1, use_cache=False)
    assert len(fake_sia) == 2 * n


def test_metadata_from_index_and_disk(fake_sia):
    recs = search_observations(269.45, 4.69, 0.1)
    rid = recs[1].observation_id
    assert get_observation_metadata(rid).observation_id == rid
    archive._index.clear()  # simulate restart: should load from disk
    assert get_observation_metadata(rid).detector == 6
    with pytest.raises(KeyError):
        get_observation_metadata("bad id; DROP TABLE")


def test_dates_and_epochs(fake_sia):
    d = get_available_observation_dates(269.45, 4.69, 0.1)
    assert d["n_observations"] == 4
    assert [x["date"] for x in d["dates"]] == ["2025-08-15", "2026-03-11", "2026-08-16"]
    assert d["dates"][0]["detectors"] == [3, 6]
    assert len(d["epochs"]) == 3
    assert d["epochs"][0]["n_per_detector"] == {"D3": 1, "D6": 1}


def test_bands(fake_sia):
    bands = get_available_bands(269.45, 4.69, 0.1)
    assert [b["band"] for b in bands] == ["SPHEREx-D3", "SPHEREx-D6"]
    d6 = bands[1]
    assert d6["wavelength_min"] == pytest.approx(4.41) and d6["n_observations"] == 1
    assert bands[0]["releases"] == ["DR1", "QR3"]


def test_release_configuration(tmp_path, monkeypatch):
    s = Settings(cache_dir=tmp_path)
    assert [r.key for r in s.resolve_releases(None)] == ["DR1", "QR3"]
    assert [r.key for r in s.resolve_releases("spherex_qr3")] == ["QR3"]
    assert len(s.resolve_releases("all")) == 4
    f = tmp_path / "rel.json"
    f.write_text(json.dumps([{"key": "DR2", "collection": "spherex_dr2", "priority": 30}]))
    s2 = Settings(cache_dir=tmp_path, releases_file=str(f), default_releases=("DR2",))
    assert s2.resolve_releases(None) == [Release("DR2", "spherex_dr2", 30)]


def test_region_contains():
    from backend.data_acquisition.archive import region_contains

    sq = "POLYGON ICRS 9 -1 11 -1 11 1 9 1"
    assert region_contains(sq, 10.0, 0.0) is True
    assert region_contains(sq, 12.0, 0.0) is False
    # wraps through RA = 0
    wrap = "POLYGON ICRS 359 -1 1 -1 1 1 359 1"
    assert region_contains(wrap, 0.2, 0.0) is True and region_contains(wrap, 2.0, 0.0) is False
    # real SPHEREx footprint (Barnard's Star image)
    real = ("POLYGON ICRS 266.6965181635169 5.487312459045988 270.1729132066038 5.171191608598052 "
            "269.9986876068837 1.684944635871196 266.5062004915302 2.0084288569816007")
    assert region_contains(real, 268.34, 3.6) is True
    assert region_contains(real, 269.45, 4.77) is True
    assert region_contains(real, 270.5, 4.77) is False
    assert region_contains(None, 1, 1) is None and region_contains("CIRCLE 1 2 3", 1, 1) is None
    assert region_contains(sq, 190.0, 0.0) is False  # opposite side of the sky


def test_search_sets_covers_position(fake_sia):
    recs = search_observations(269.45, 4.69, 0.1)
    assert all(r.covers_position is None for r in recs)  # canned rows have a dummy region


def test_metadata_tap_fallback_refetches_sia_row(monkeypatch):
    from types import SimpleNamespace

    row = sia_row("2025W33_A", 6, 60902.86, access_url="https://irsa.ipac.caltech.edu/datalink/links?ID=x")

    class FakeTable(list):
        colnames = list(row)

    class FakeTap:
        def __init__(self, url):
            pass

        def run_sync(self, adql):
            assert "obs_id = '2025W33_A'" in adql and "SPHEREx-D6" in adql
            return SimpleNamespace(to_table=lambda: FakeTable([row]))

    import pyvo

    monkeypatch.setattr(pyvo.dal, "TAPService", FakeTap)
    monkeypatch.setattr(archive, "_sia_query", lambda *a: [sia_row("2025W33_A", 6, 60902.86)])
    rec = get_observation_metadata("2025W33_A_D6")
    assert rec.fits_url.endswith("2025W33_AD6.fits")  # direct URL from SIA2, not DataLink
