import threading

import pytest

from backend.data_acquisition import download, http
from backend.data_acquisition.archive import row_to_record
from backend.data_acquisition.config import get_settings
from backend.data_acquisition.download import (DownloadError, build_cutout_url, download_cutout,
                                               download_cutouts, download_observation)
from backend.data_acquisition.fits_meta import decode_flag_definitions, extract_fits_metadata
from backend.data_acquisition.http import HttpError, request_with_retries
from tests.conftest import make_spherex_fits, sia_row

URL = "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/x/level2_A_D6_spx.fits"


class FakeResp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {"Content-Length": str(len(body))}
        self.text = body[:200].decode(errors="replace")

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass


class FakeSession:
    """Returns queued responses; records the URLs requested."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.lock = threading.Lock()

    def request(self, method, url, **kw):
        with self.lock:
            self.urls.append(url)
            r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def session(monkeypatch):
    holder = {}

    def install(responses):
        s = FakeSession(responses)
        monkeypatch.setattr(download, "get_session", lambda: s)
        holder["s"] = s
        return s

    return install


def test_build_cutout_url():
    u = build_cutout_url(URL, 133.7951292, -7.2451472, 0.05)
    assert u == URL + "?center=133.7951292,-7.2451472d&size=0.050000"
    with pytest.raises(DownloadError):
        build_cutout_url("https://evil.example.com/x.fits", 1, 2, 0.1)
    with pytest.raises(DownloadError):
        build_cutout_url(URL + "?a=b", 1, 2, 0.1)


def test_retry_on_503_then_success():
    s = FakeSession([FakeResp(503, b"Server busy, retry later."), FakeResp(200, b"ok")])
    r = request_with_retries(s, "GET", "https://x", retries=3, backoff=0)
    assert r.status_code == 200 and len(s.urls) == 2


def test_no_retry_on_404_and_give_up():
    s = FakeSession([FakeResp(404, b"nope")])
    with pytest.raises(HttpError) as e:
        request_with_retries(s, "GET", "https://x", retries=3, backoff=0)
    assert e.value.status == 404 and len(s.urls) == 1
    s = FakeSession([FakeResp(503, b"busy")])
    with pytest.raises(HttpError):
        request_with_retries(s, "GET", "https://x", retries=2, backoff=0)
    assert len(s.urls) == 3


def test_download_cutout_writes_validates_and_caches(session, fits_bytes):
    s = session([FakeResp(503, b"Server busy, retry later."), FakeResp(200, fits_bytes)])
    info = download_cutout(URL, 133.8, -7.2, 0.03)
    assert info["from_cache"] is False
    assert info["local_path"].endswith(".fits")
    assert info["metadata"]["detector"] == 6
    assert info["metadata"]["date_obs"].startswith("2025-08-15")
    assert info["metadata"]["quality_flags"] == ["HOT"]  # FULLSAMPLE is informational
    assert len(s.urls) == 2  # one retry
    again = download_cutout(URL, 133.8, -7.2, 0.03)
    assert again["from_cache"] is True and len(s.urls) == 2  # no new request
    download_cutout(URL, 133.8, -7.2, 0.03, force=True)
    assert len(s.urls) == 3


def test_download_cutout_rejects_non_fits(session):
    session([FakeResp(200, b"<html>error page</html>")])
    with pytest.raises(DownloadError):
        download_cutout(URL, 133.8, -7.2, 0.03)
    assert not list(get_settings().cutout_dir.rglob("*.fits"))  # nothing half-written kept
    assert not list(get_settings().cutout_dir.rglob("*.part"))


def test_download_cutout_detects_truncation(session, fits_bytes):
    session([FakeResp(200, fits_bytes[:1000], headers={"Content-Length": str(len(fits_bytes))})])
    with pytest.raises(DownloadError, match="Truncated"):
        download_cutout(URL, 133.8, -7.2, 0.03)


def test_download_cutouts_batch_collects_errors_and_enriches(session, fits_bytes, monkeypatch):
    good = sia_row("A", 6)
    bad = sia_row("B", 6)
    from backend.data_acquisition.config import Release

    rel = Release("DR1", "spherex_dr1", 20)
    recs = [row_to_record(good, rel), row_to_record(bad, rel)]

    def fake_fetch(url, dest, progress=None):
        if "/BD6" in url:
            raise HttpError("HTTP 404", status=404)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fits_bytes)
        return len(fits_bytes), "sha"

    monkeypatch.setattr(download, "_fetch_to_file", fake_fetch)
    events = []
    res = download_cutouts(recs, 133.8, -7.2, 0.03, progress=events.append, max_workers=2)
    out = res.to_dict()
    assert out["n_ok"] == 1 and out["n_failed"] == 1 and out["errors"][0]["observation_id"] == "B_D6"
    r = res.records[0]
    assert r.local_path and r.quality_flags == ["HOT"] and r.quality["l2_dqa"] == "Pass"
    assert {e["status"] for e in events} == {"downloaded", "failed"}
    assert events[-1]["done"] == 2


def test_download_cutouts_limit(monkeypatch):
    get_settings().max_cutouts_per_request = 1
    from backend.data_acquisition.config import Release

    rel = Release("DR1", "spherex_dr1", 20)
    recs = [row_to_record(sia_row("A", 1), rel), row_to_record(sia_row("B", 1), rel)]
    with pytest.raises(DownloadError, match="Too many"):
        download_cutouts(recs, 1, 2, 0.03)


def test_download_observation_full(session, fits_bytes, tmp_path):
    s = session([FakeResp(200, fits_bytes)])
    out = download_observation(URL, tmp_path / "full")
    assert out["from_cache"] is False and out["bytes"] == len(fits_bytes)
    assert download_observation(URL, tmp_path / "full")["from_cache"] is True
    assert len(s.urls) == 1


def test_flag_decoding_and_metadata(tmp_path):
    p = tmp_path / "x.fits"
    p.write_bytes(make_spherex_fits(flags_at_center=1 | 2097152))
    from astropy.io import fits

    with fits.open(p) as h:
        defs = decode_flag_definitions(h["FLAGS"].header)
    assert defs["SOURCE"]["bit"] == 21 and defs["HOT"]["value"] == 1024
    meta = extract_fits_metadata(p, 133.8, -7.2)
    assert meta["target_in_image"] is True
    assert meta["flags"]["target_pixel_flags"] == ["SOURCE", "TRANSIENT"]
    assert meta["quality_flags"] == ["TRANSIENT"]
    assert meta["pixel_scale_arcsec"] == pytest.approx(6.15, rel=1e-3)
    assert meta["center_ra"] == pytest.approx(133.8, abs=1e-6)
    off = extract_fits_metadata(p, 140.0, 10.0)
    assert off["target_in_image"] is False and off["quality_flags"] == []


def test_batch_skips_images_not_covering_position(monkeypatch, fits_bytes):
    from backend.data_acquisition.config import Release

    rel = Release("DR1", "spherex_dr1", 20)
    a, b = row_to_record(sia_row("A", 6), rel), row_to_record(sia_row("B", 6), rel)
    a.covers_position, b.covers_position = True, False

    def fake_fetch(url, dest, progress=None):
        assert "/BD6" not in url
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fits_bytes)
        return len(fits_bytes), "sha"

    monkeypatch.setattr(download, "_fetch_to_file", fake_fetch)
    out = download_cutouts([a, b], 133.8, -7.2, 0.03).to_dict()
    assert out["n_ok"] == 1 and out["n_skipped"] == 1 and out["skipped"][0]["observation_id"] == "B_D6"


def test_422_becomes_clear_error(monkeypatch):
    def fake_fetch(url, dest, progress=None):
        raise HttpError("HTTP 422", status=422)

    monkeypatch.setattr(download, "_fetch_to_file", fake_fetch)
    with pytest.raises(DownloadError, match="outside this image"):
        download_cutout(URL, 133.8, -7.2, 0.03)
