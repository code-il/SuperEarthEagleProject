"""Synthetic SPHEREx-like frames with known truth, for processing tests."""
from __future__ import annotations

import io

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from tests.conftest import FLAG_DEFS

SCALE = 6.15 / 3600.0


def frame_wcs(ra: float, dec: float, n: int, rot_deg: float) -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    th = np.radians(rot_deg)
    w.wcs.cd = np.array([[-np.cos(th), np.sin(th)], [np.sin(th), np.cos(th)]]) * SCALE
    w.wcs.set()
    return w


def make_frame(ra=150.0, dec=10.0, n=41, rot_deg=0.0, stars=(), zodi=0.5, sky=0.05, noise=0.002,
               fwhm=9.0, detector=6, mjd=60900.0, date_obs="2025-08-13T00:00:00.000",
               flags=None, nan_pixels=(), obs_id="SYN_1", seed=0, wave=None) -> bytes:
    """FITS bytes. ``stars`` = [(ra, dec, peak MJy/sr), ...]. Pixel (row, col) lists set flags/NaN."""
    rng = np.random.default_rng(seed)
    w = frame_wcs(ra, dec, n, rot_deg)
    yy, xx = np.indices((n, n))
    img = np.full((n, n), zodi + sky) + rng.normal(0, noise, (n, n))
    sig = fwhm / 2.3548 / 6.15
    for sra, sdec, peak in stars:
        x, y = w.world_to_pixel_values(sra, sdec)
        img += peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sig ** 2))
    for (r, c) in nan_pixels:
        img[r, c] = np.nan
    fl = np.zeros((n, n), np.int32) if flags is None else flags.astype(np.int32)
    whdr = w.to_header()

    prim = fits.PrimaryHDU()
    prim.header["VERSION"] = "test"
    im = fits.ImageHDU(img.astype("f4"), header=whdr, name="IMAGE")
    for k, v in {"BUNIT": "MJy / sr", "DETECTOR": detector, "OBSID": obs_id, "DATE-OBS": date_obs,
                 "MJD-OBS": mjd, "XPOSURE": 113.6, "L1DQAFLG": "Pass", "L2DQAFLG": "Pass",
                 "PSF_FWHM": fwhm}.items():
        im.header[k] = v
    fh = fits.ImageHDU(fl, header=whdr, name="FLAGS")
    for i, (name, val, desc) in enumerate(FLAG_DEFS):
        fh.header[f"MSKN{i:04d}"] = name
        fh.header[f"MSKM{i:04d}"] = val
        fh.header[f"MSKD{i:04d}"] = desc
    var = fits.ImageHDU(np.full((n, n), noise ** 2, "f4"), header=whdr, name="VARIANCE")
    zh = fits.ImageHDU(np.full((n, n), zodi, "f4"), header=whdr, name="ZODI")
    hdul = fits.HDUList([prim, im, fh, var, zh])
    buf = io.BytesIO()
    hdul.writeto(buf)
    return buf.getvalue()


def write_frame(path, **kw):
    path.write_bytes(make_frame(**kw))
    return path
