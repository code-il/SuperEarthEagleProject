import pytest
from fastapi.testclient import TestClient

from backend.api import data_routes
from backend.api.main import app
from backend.data_acquisition import archive, download
from tests.conftest import sia_row
from tests.test_coords import SESAME_XML


@pytest.fixture
def client(monkeypatch, fits_bytes):
    rows = [sia_row("2025W33_A", 6, 60902.86), sia_row("2026W10_B", 6, 61110.10)]
    monkeypatch.setattr(archive, "_sia_query", lambda c, *a: [dict(r) for r in rows] if c == "spherex_dr1" else [])

    class Resp:
        text = SESAME_XML

    from backend.data_acquisition import coords

    monkeypatch.setattr(coords, "request_with_retries", lambda *a, **k: Resp())

    def fake_fetch(url, dest, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fits_bytes)
        return len(fits_bytes), "sha"

    monkeypatch.setattr(download, "_fetch_to_file", fake_fetch)
    return TestClient(app)


def test_health_releases_targets(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert [r["key"] for r in client.get("/api/releases").json()["releases"]][:2] == ["DR1_DEEP", "DR1"]
    keys = [t["key"] for t in client.get("/api/targets").json()["targets"]]
    assert keys == ["barnards-star", "epsilon-indi-b", "wise-0855", "rw-aurigae", "r-draconis"]


def test_observations_by_coordinates(client):
    j = client.get("/api/observations", params={"ra": "08h55m10.831s", "dec": "-7d14m42.53s", "fov": 0.05}).json()
    assert j["count"] == 2 and j["query"]["kind"] == "coordinates"
    assert len(j["epochs"]) == 2
    assert "target_predicted_ra" not in j["observations"][0]


def test_observations_by_name_have_predictions(client):
    j = client.post("/api/search", json={"q": "Barnard's Star", "fov": 0.05}).json()
    assert j["query"]["name"] == "NAME Barnard's star"
    o0, o1 = j["observations"]
    assert o1["target_predicted_dec"] > o0["target_predicted_dec"]  # moving north


def test_errors(client):
    assert client.get("/api/observations", params={"ra": 400, "dec": 0}).status_code == 400
    assert client.get("/api/observations").status_code == 400
    assert client.get("/api/observations", params={"ra": 1, "dec": 1, "fov": 50}).status_code == 400
    assert client.get("/api/observations/garbage").status_code == 404
    assert client.get("/api/files/cutouts/../../secret.fits").status_code == 404


def test_dates_bands_and_metadata(client):
    p = {"ra": 133.8, "dec": -7.2, "fov": 0.05}
    d = client.get("/api/observations/dates", params=p).json()
    assert [x["date"] for x in d["dates"]] == ["2025-08-15", "2026-03-11"]
    b = client.get("/api/observations/bands", params=p).json()["bands"]
    assert b[0]["band"] == "SPHEREx-D6"
    m = client.get("/api/observations/2025W33_A_D6").json()
    assert m["detector"] == 6


def test_cutouts_and_file_serving(client):
    j = client.post("/api/cutouts", json={"ra": 133.8, "dec": -7.2, "fov": 0.03}).json()
    assert j["n_ok"] == 2 and j["n_failed"] == 0
    o = j["observations"][0]
    assert o["quality_flags"] == ["HOT"] and o["file_url"].startswith("/api/files/cutouts/")
    r = client.get(o["file_url"])
    assert r.status_code == 200 and r.content.startswith(b"SIMPLE")
    j2 = client.post("/api/cutouts", json={"ra": 133.8, "dec": -7.2, "fov": 0.03,
                                           "observation_ids": ["2026W10_B_D6"]}).json()
    assert j2["n_ok"] == 1 and j2["n_from_cache"] == 1
    bad = client.post("/api/cutouts", json={"ra": 133.8, "dec": -7.2, "fov": 0.03, "observation_ids": ["nope_D1"]})
    assert bad.status_code == 404


def test_cutouts_track_target(client):
    j = client.post("/api/cutouts", json={"q": "Barnard's Star", "fov": 0.03, "track_target": True}).json()
    c0, c1 = (o["cutout"] for o in j["observations"])
    assert c1["center_dec"] > c0["center_dec"]
    bad = client.post("/api/cutouts", json={"ra": 1, "dec": 1, "fov": 0.03, "track_target": True})
    assert bad.status_code == 400
