import pytest
from fastapi.testclient import TestClient

from backend import catalogs
from backend.api.main import app

GAIA_CSV = ("source_id,ra,dec,pmra,pmdec,parallax,phot_g_mean_mag\n"
            "4472832130942575872,269.4485,4.7394,-801.55,10362.39,546.98,8.19\n"
            "1,269.45,4.70,,,,15.0\n")
CW_CSV = ("source_name,ra,dec,pmra,pmdec,w1mpro,w2mpro,meanobsmjd,cc_flags\n"
          "J085510.74-071442.5,133.7948,-7.2451,-8.1,0.67,17.3,13.9,57200.0,\n")
# A saturated star (W2 8.5) with a spurious 2.5"/yr "mover" 18" away, a far real mover,
# and a flagged artefact.
HALO_CSV = ("source_name,ra,dec,pmra,pmdec,w1mpro,w2mpro,meanobsmjd,cc_flags\n"
            "J1,269.4470,4.7447,0.01,0.02,8.9,8.5,57200.0,HHH0\n"
            "J2,269.4520,4.7447,-2.47,0.01,13.0,12.6,57200.0,\n"
            "J3,269.5200,4.8100,-3.00,1.00,16.0,15.5,57200.0,\n"
            "J4,269.5300,4.8200,2.00,0.00,16.0,15.9,57200.0,dD00\n")


@pytest.fixture
def fake_tap(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, text):
            self.text = text

    def fake(session, method, url, data=None, timeout=None, params=None, **kw):
        calls.append((url, data["QUERY"] if data else params))
        return Resp(GAIA_CSV if "gea.esac" in url else CW_CSV)

    monkeypatch.setattr(catalogs, "request_with_retries", fake)
    return calls


def test_gaia_propagation(fake_tap):
    r = catalogs.cone_search("gaia", 269.45, 4.72, 0.05, epoch="2026-03-01")
    assert r["count"] == 2 and "gaiadr3.gaia_source" in fake_tap[0][1]
    b = r["sources"][0]
    assert b["id"].startswith("Gaia DR3") and b["mag_band"] == "G"
    # ~10.17 yr * 10.36"/yr = ~105" north
    assert (b["dec_at_epoch"] - b["dec"]) * 3600 == pytest.approx(105.4, abs=0.5)
    assert b["ra_at_epoch"] < b["ra"]
    s = r["sources"][1]  # no proper motion: unchanged
    assert (s["ra_at_epoch"], s["dec_at_epoch"]) == (s["ra"], s["dec"])
    catalogs.cone_search("gaia", 269.45, 4.72, 0.05)
    assert len(fake_tap) == 1  # cached


def test_catwise_units(fake_tap):
    r = catalogs.cone_search("catwise", "08h55m10.8s", "-7d14m42s", 0.02, epoch="2026-01-01")
    s = r["sources"][0]
    assert s["pm_ra_cosdec_mas_yr"] == pytest.approx(-8100.0) and s["mag_band"] == "W2"
    assert fake_tap[0][0].endswith("/SCS") and fake_tap[0][1]["table"] == "catwise_2020"


def test_catwise_falls_back_to_tap(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, text):
            self.text = text

    def fake(session, method, url, data=None, timeout=None, params=None, **kw):
        calls.append(url)
        return Resp("<html>busy</html>" if url.endswith("/SCS") else CW_CSV)

    monkeypatch.setattr(catalogs, "request_with_retries", fake)
    r = catalogs.cone_search("catwise", 133.79, -7.24, 0.02, use_cache=False)
    assert r["count"] == 1 and calls[-1].endswith("/TAP/sync")


def test_catalog_route_errors(fake_tap):
    c = TestClient(app)
    assert c.get("/api/catalog/gaia", params={"ra": 269.45, "dec": 4.72, "radius": 0.02}).json()["count"] == 2
    assert c.get("/api/catalog/foo", params={"ra": 1, "dec": 1}).status_code == 400
    assert c.get("/api/catalog/gaia", params={"ra": 1, "dec": 1, "radius": 3}).status_code == 422


def test_catwise_suspect_motions_near_bright_stars(monkeypatch):
    class Resp:
        text = HALO_CSV

    monkeypatch.setattr(catalogs, "request_with_retries", lambda *a, **k: Resp())
    r = catalogs.cone_search("catwise", 269.46, 4.76, 0.1, epoch="2026-03-01", use_cache=False)
    by = {s["id"]: s for s in r["sources"]}
    assert by["CWISE J2"]["pm_suspect"] is True       # halo of the W2=8.5 star
    assert by["CWISE J3"]["pm_suspect"] is False      # far from it, clean flags
    assert by["CWISE J4"]["pm_suspect"] is True       # artefact flags
    assert by["CWISE J1"]["pm_suspect"] is False      # slow
    j2 = by["CWISE J2"]
    assert (j2["ra_at_epoch"], j2["dec_at_epoch"]) == (j2["ra"], j2["dec"])  # not moved
    assert by["CWISE J3"]["ra_at_epoch"] != by["CWISE J3"]["ra"]
