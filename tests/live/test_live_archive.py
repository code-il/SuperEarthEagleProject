"""Live tests against IRSA and CDS. Run with:  pytest -m live

They are excluded from the default run (see pytest.ini) because they need
network access and depend on the archive being up.
"""
import pytest

from backend.data_acquisition import (download_cutout, get_available_bands,
                                      get_observation_metadata, resolve_name, search_observations)

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def wise0855():
    t = resolve_name("WISE J085510.83-071442.5")
    c = t.position_at("2026-01-01")
    return t, c.ra.deg, c.dec.deg


def test_resolve_has_proper_motion(wise0855):
    t, _, _ = wise0855
    assert t.has_proper_motion and abs(t.pm_ra_cosdec_mas_yr) > 7000


def test_search_bands_and_cutout(wise0855):
    t, ra, dec = wise0855
    recs = search_observations(ra, dec, 0.05)
    assert len(recs) > 50
    assert recs == sorted(recs, key=lambda r: r.mjd_start)
    assert {b["detector"] for b in get_available_bands(ra, dec, 0.05)} == {1, 2, 3, 4, 5, 6}
    rec = next(r for r in recs if r.detector == 6 and r.covers_position)
    info = download_cutout(rec.fits_url, ra, dec, 0.02, record=rec)
    meta = info["metadata"]
    assert meta["extensions"][:3] == ["PRIMARY", "IMAGE", "FLAGS"]
    assert meta["date_obs"][:16] == rec.observation_time[:16]
    assert 4.3 < meta["wavelength_um"] < 5.1
    assert get_observation_metadata(rec.observation_id).local_path == info["local_path"]
