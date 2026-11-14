"""Shared fixtures. Unit tests never touch the network; live tests are marked ``live``."""
from __future__ import annotations

import io

import numpy as np
import pytest
from astropy.io import fits

from backend.data_acquisition import archive
from backend.data_acquisition.config import Settings, set_settings


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits the real IRSA/CDS services (run with -m live)")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Fresh settings + empty cache directory for every test."""
    for var in ("SPHEREX_RELEASES_FILE", "SPHEREX_DEFAULT_RELEASES"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(cache_dir=tmp_path / "cache", http_retries=2, http_backoff_s=0.0)
    set_settings(s)
    archive._index.clear()
    from backend.api import analysis_routes, main

    monkeypatch.setattr(main.limiter, "per_minute", 0)  # rate limiting has its own test
    analysis_routes._RESULTS.clear()
    yield s
    archive._index.clear()
    analysis_routes._RESULTS.clear()


FLAG_DEFS = [("TRANSIENT", 1, "Transient detected during SUR"), ("OVERFLOW", 2, "Overflow"),
             ("HOT", 1024, "Hot pixel"), ("FULLSAMPLE", 4096, "full sample"),
             ("SOURCE", 2097152, "Pixel mapped to a known source")]


def make_spherex_fits(ra: float = 133.8, dec: float = -7.2, n: int = 21, flags_at_center: int = 1024 | 4096,
                      detector: int = 6, date_obs: str = "2025-08-15T20:45:11.337") -> bytes:
    """Build a small FITS that mimics a SPHEREx L2 cutout (same extensions/keywords)."""
    def wcs_cards(h: fits.Header) -> None:
        h["CTYPE1"], h["CTYPE2"] = "RA---TAN", "DEC--TAN"
        h["CRVAL1"], h["CRVAL2"] = ra, dec
        h["CRPIX1"] = h["CRPIX2"] = (n + 1) / 2.0
        h["CDELT1"], h["CDELT2"] = -6.15 / 3600, 6.15 / 3600
        h["CUNIT1"] = h["CUNIT2"] = "deg"

    primary = fits.PrimaryHDU()
    primary.header["VERSION"] = "7.0.5"
    img = fits.ImageHDU(np.random.default_rng(0).normal(1, 0.1, (n, n)).astype("f4"), name="IMAGE")
    h = img.header
    h["BUNIT"] = "MJy / sr"
    h["DETECTOR"] = detector
    h["OBSID"] = "2025W33_2C_0321_1"
    h["DATE-OBS"] = date_obs
    h["MJD-OBS"] = 60902.864714549
    h["XPOSURE"] = 113.5826
    h["L1DQAFLG"] = "Pass"
    h["L2DQAFLG"] = "Pass"
    h["PSF_FWHM"] = 9.0
    wcs_cards(h)
    fl = np.zeros((n, n), dtype="i4")
    c = n // 2
    fl[c, c] = flags_at_center
    fl[0, 0] = 2
    flags = fits.ImageHDU(fl, name="FLAGS")
    for i, (name, val, desc) in enumerate(FLAG_DEFS):
        flags.header[f"MSKN{i:04d}"] = name
        flags.header[f"MSKM{i:04d}"] = val
        flags.header[f"MSKD{i:04d}"] = desc
    wcs_cards(flags.header)
    var = fits.ImageHDU(np.full((n, n), 0.01, "f4"), name="VARIANCE")
    zodi = fits.ImageHDU(np.full((n, n), 0.5, "f4"), name="ZODI")
    hdul = fits.HDUList([primary, img, flags, var, zodi])
    buf = io.BytesIO()
    hdul.writeto(buf)
    return buf.getvalue()


@pytest.fixture
def fits_bytes() -> bytes:
    return make_spherex_fits()


def sia_row(obs_id="2025W33_2C_0321_1", det=3, t_min=60902.864715, collection="spherex_dr1", **extra):
    row = {
        "obs_id": obs_id,
        "obs_publisher_did": f"ivo://irsa.ipac/{collection}?{obs_id}/D{det}",
        "obs_collection": collection, "s_ra": 268.34, "s_dec": 3.61, "s_fov": 3.485,
        "s_region": "POLYGON ICRS 1 2 3 4", "s_resolution": 3.075, "s_pixel_scale": 6.15,
        "s_xel1": 2040, "s_xel2": 2040, "t_min": t_min, "t_max": t_min + 0.00133,
        "t_exptime": 113.58, "em_min": 1.6301e-06, "em_max": 2.4346e-06, "em_res_power": 40.6,
        "energy_bandpassname": f"SPHEREx-D{det}", "calib_level": 2, "instrument_name": "SPHEREx",
        "facility_name": "SPHEREx",
        "access_url": f"https://irsa.ipac.caltech.edu/ibe/data/spherex/x/{obs_id}D{det}.fits",
        "access_format": "image/fits", "access_estsize": 61949, "target_name": "SPS_1",
        "obs_release_date": "2026-10-04 05:45:39", "dataproduct_type": "image",
        "dataproduct_subtype": "science",
        "cloud_access": '{"aws": {"bucket_name": "nasa-irsa-spherex", "key": "k", "region": "us-east-1"}}',
    }
    row.update(extra)
    return row
