"""Integration (Workstream 5): the acceptance workflow through the one API, offline.

Archive queries and downloads are mocked with synthetic SPHEREx frames (two
stars, one of which moves between visits); everything else runs for real:
search (P1) -> processing, comparison (P2) -> analysis (P4) -> the contracts
the viewer (P3) reads -> serving the built viewer.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.analysis import summarise_source
from backend.api import main
from tests.test_processing_pipeline import DEC, RA, mocked_archive  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
FIELD = {"ra": RA, "dec": DEC, "fov": 0.04, "detectors": [6]}


@pytest.fixture
def client(mocked_archive):  # noqa: F811
    return TestClient(main.app)


def test_acceptance_workflow(client):
    c = client
    # 1. the site and its API are up
    assert c.get("/api/health").json()["status"] == "ok"
    v = c.get("/api/version").json()
    assert {"app", "processing", "analysis"} <= set(v)
    assert [t["key"] for t in c.get("/api/targets").json()["targets"]][2] == "wise-0855"

    # 2-3. search and retrieve the observations (Person 1)
    s = c.post("/api/search", json=FIELD).json()
    assert s["count"] == 3
    mjds = [o["mjd_start"] for o in s["observations"]]
    assert mjds == sorted(mjds)
    oid = s["observations"][0]["observation_id"]
    meta = c.get(f"/api/observations/{oid}").json()
    assert meta["observation_id"] == oid

    # 4. chronological, processed frames with the metadata the info panel shows (Person 2)
    p = c.post("/api/process", json={**FIELD, "make_gifs": False}).json()
    obs = p["observations"]
    assert [o["mjd"] for o in obs] == sorted(o["mjd"] for o in obs)
    for k in ("observation_time", "wavelength", "detector", "data_release", "quality_flags", "fits_url", "image_url",
              "processing_method", "wavelength_source"):
        assert k in obs[0], k
    assert c.get(obs[0]["fits_url"]).content[:6] == b"SIMPLE"

    # 7-8. compare two dates: difference image
    d = c.post("/api/compare", json={**FIELD, "a": {"epoch_index": 1}, "b": {"epoch_index": 0}}).json()
    assert d["mode"] == "difference" and c.get(d["fits_url"]).status_code == 200

    # 10-11. run the analysis; positions and motion (Person 4)
    q = {"ra": RA, "dec": DEC, "fov": 0.04, "catalogs": "none", "skybot": False}
    a = c.get("/api/analysis", params={**q, "full": False}).json()
    assert {"sources", "light_curves", "spectra", "results_summary", "summary", "warnings", "disclaimer"} <= set(a)
    assert a["summary"]["n_observations"] == 3
    assert a["results_summary"], "no sources detected in the synthetic field"
    sid = a["results_summary"][0]["source_id"]
    q2 = {"ra": RA, "dec": DEC, "fov": 0.04, "catalogs": "none"}
    full = c.get(f"/api/analysis/{sid}", params=q2).json()
    assert full["source_id"] == sid and full["detections"]
    det = full["detections"][0]
    assert {"mjd", "ra", "dec", "ra_err_total_arcsec"} <= set(det)
    assert "light_curve" in c.get(f"/api/light-curve/{sid}", params=q2).json()
    sp = c.get(f"/api/spectrum/{sid}", params=q2).json()
    assert "spectrum" in sp and "visit_ratios" in sp
    assert c.get("/api/analysis/S999", params=q2).status_code == 404
    det2 = c.post("/api/analysis/detect", json={"ra": RA, "dec": DEC, "fov": 0.04, "catalogs": [], "skybot": False}).json()
    assert len(det2["results_summary"]) == len(a["results_summary"])
    # never a discovery claim
    assert all(r["status"] in ("known", "candidate") for r in a["results_summary"])
    assert "none is a discovery" in a["disclaimer"].lower()


def test_errors_are_json_and_helpful(client):
    c = client
    r = c.post("/api/process", json={"ra": 400, "dec": 0})
    assert r.status_code == 400 and "detail" in r.json()
    assert c.get("/api/analysis", params={"ra": RA, "dec": DEC, "fov": 2}).status_code == 422
    assert c.get("/api/nope").status_code == 404
    assert "x-request-id" in {k.lower() for k in c.get("/api/health").headers}


def test_rate_limit(client, monkeypatch):
    lim = main.RateLimiter(2)
    assert lim.allow("a", 0)[0] and lim.allow("a", 1)[0]
    ok, retry = lim.allow("a", 2)
    assert not ok and 0 < retry <= 60
    assert lim.allow("b", 2)[0] and lim.allow("a", 61)[0]
    monkeypatch.setattr(main, "limiter", main.RateLimiter(1))
    assert client.get("/api/targets").status_code == 200  # not rate limited (no archive access)
    assert client.get("/api/observations", params={"ra": RA, "dec": DEC}).status_code == 200
    r = client.get("/api/observations", params={"ra": RA, "dec": DEC})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_viewer_contract_matches_backend():
    """The viewer's SourceSummary type lists exactly the fields the backend sends (no silent format drift)."""
    ts = (ROOT / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")
    block = re.search(r"export interface SourceSummary \{(.*?)\n\}", ts, re.S).group(1)
    fields = set(re.findall(r"^  (\w+)\??:", block, re.M))
    rec = {"source_id": "S1", "ra": 1.0, "dec": 2.0, "classification": "stationary", "label": "stationary",
           "confidence": 0.5, "status": "candidate"}
    assert fields == set(summarise_source(rec))


def test_built_viewer_is_served(tmp_path, monkeypatch):
    """With a viewer build present, client-side routes fall back to index.html and /api stays the API."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    monkeypatch.setenv("SPHEREX_FRONTEND_DIST", str(dist))
    import importlib
    m = importlib.reload(main)
    try:
        c = TestClient(m.app)
        for path in ("/", "/explore?q=WISE%200855", "/about"):
            r = c.get(path)
            assert r.status_code == 200 and "id=root" in r.text, path
        assert c.get("/favicon.svg").text == "<svg/>"
        assert c.get("/api/does-not-exist").status_code == 404
        assert c.get("/../secret").status_code in (200, 404)  # never escapes dist (falls back to index.html)
    finally:
        monkeypatch.delenv("SPHEREX_FRONTEND_DIST")
        importlib.reload(main)
