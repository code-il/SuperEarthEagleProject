"""Checks for the fixes made after the scientific review of the image pipeline.

Variance through resampling (squared weights), conservative flag propagation,
ratio uncertainties, 2-D background, kept intermediate products, alignment
statistics and wavelength-safe stacking.
"""
import numpy as np
import pytest

from backend.image_processing.align import (alignment_summary, bilinear_contributors, make_reference_wcs,
                                            propagate_bilinear, reproject_observation)
from backend.image_processing.combine import divide_images
from backend.image_processing.pipeline import ProcessedObservation, split_by_wavelength
from backend.image_processing.prepare import prepare_image
from tests.processing_fixtures import frame_wcs

SCALE_DEG = 6.15 / 3600


def _shifted(n_in=31, n_out=21, dx=0.5, dy=0.0):
    """Input grid and an output grid offset by (dx, dy) input pixels, same scale, north up."""
    w_in, _ = make_reference_wcs(150.0, 10.0, n_in * SCALE_DEG)
    w_out, shape = make_reference_wcs(150.0, 10.0, n_out * SCALE_DEG)
    w_out.wcs.crpix = [w_out.wcs.crpix[0] - dx, w_out.wcs.crpix[1] - dy]
    w_out.wcs.set()
    return w_in, w_out, shape


def test_bilinear_weights_match_reproject_interp():
    """Our contributor weights reproduce reproject_interp's bilinear science values."""
    w_in = frame_wcs(150.0, 10.0, 41, 23.0)
    w_out, shape = make_reference_wcs(150.0, 10.0, 0.04)
    rng = np.random.default_rng(1)
    img = rng.normal(size=(41, 41))
    ref, fp = reproject_observation(img, w_in, w_out, shape, "interp")
    idx, w = bilinear_contributors(w_in, w_out, shape, img.shape)
    ours = (w * np.where(idx >= 0, img.ravel()[np.where(idx >= 0, idx, 0)], 0)).sum(axis=0).reshape(shape)
    inner = (fp > 0) & np.isfinite(ref) & (w.sum(axis=0).reshape(shape) > 0.999)
    assert inner.sum() > 100
    assert np.allclose(ours[inner], ref[inner], atol=1e-6)


def test_variance_uses_squared_weights():
    w_in, w_out, shape = _shifted(dx=0.5, dy=0.5)  # every output pixel = mean of 4 inputs
    var = np.full((31, 31), 4.0)
    pr = propagate_bilinear(w_in, w_out, shape, valid=np.ones((31, 31), bool), variance=var)
    # sum w^2 v = 4 * (1/4)^2 * 4 = 1 (interpolating the variance would give 4)
    assert np.nanmedian(pr.variance) == pytest.approx(1.0, rel=1e-3)
    # ... and the correlation factor restores the large-area variance (c_i = 1, so factor 4)
    assert pr.noise_correlation == pytest.approx(4.0, rel=0.1)
    # no shift: weights are (1, 0, 0, 0), variance unchanged, no correlation
    w_in, w_out, shape = _shifted(dx=0.0, dy=0.0)
    pr0 = propagate_bilinear(w_in, w_out, shape, valid=np.ones((31, 31), bool), variance=var)
    assert np.nanmedian(pr0.variance) == pytest.approx(4.0, rel=1e-3)
    assert pr0.noise_correlation == pytest.approx(1.0, rel=0.05)


def test_flags_or_over_contributors_and_valid_weight():
    w_in, w_out, shape = _shifted(dx=0.5, dy=0.0)
    bits = np.zeros((31, 31), np.int64)
    bits[15, 15] = 8
    valid = bits == 0
    pr = propagate_bilinear(w_in, w_out, shape, valid=valid, bits=bits)
    hit = np.argwhere(pr.bits == 8)
    # with a half-pixel shift in x the bad pixel feeds two output pixels; nearest-neighbour keeps one
    assert len(hit) == 2 and hit[0][0] == hit[1][0]
    assert np.all(pr.valid_weight[pr.bits == 8] == pytest.approx(0.5))
    assert np.nanmin(pr.valid_weight[pr.bits == 0]) == pytest.approx(1.0)


def test_ratio_variance_stable_and_positive_denominator():
    a = np.array([[0.0, 10.0, 5.0]])
    b = np.array([[10.0, -10.0, 10.0]])
    v = np.full((1, 3), 1.0)
    r = divide_images(a, b, v, v, min_snr_b=5)
    # A = 0: var = var_A / B^2 = 0.01 (the old R^2(...) form was 0 * inf = NaN)
    assert r.variance[0, 0] == pytest.approx(0.01)
    assert np.isnan(r.data[0, 1])  # negative denominator rejected even though |B|/sigma = 10
    assert r.variance[0, 2] == pytest.approx(1 / 100 + 25 / 1e4)


def test_mesh_background_follows_gradient_and_products_kept():
    ny = nx = 64
    yy, xx = np.mgrid[0:ny, 0:nx]
    sky = 0.2 + 0.01 * xx  # gradient across the cutout
    zodi = np.full((ny, nx), 0.5)
    img = sky + zodi
    img[30:33, 30:33] += 5  # a star
    med = prepare_image(img, zodi=zodi, background="zodi+median")
    mesh = prepare_image(img, zodi=zodi, background="zodi+mesh")
    inner = np.s_[8:56, 8:56]
    resid_mesh = np.nanstd(np.where(np.abs(mesh.data) < 1, mesh.data, np.nan)[inner])
    resid_med = np.nanstd(np.where(np.abs(med.data) < 1, med.data, np.nan)[inner])
    assert resid_mesh < 0.02 < resid_med  # the constant leaves the 0.48 MJy/sr gradient in
    assert mesh.data[31, 31] > 4.5  # the star survives
    # the original and the ZODI-subtracted image are both kept, untouched
    assert np.allclose(mesh.original, img)
    assert np.allclose(mesh.zodi_subtracted, img - zodi)
    assert mesh.background_map is not None and mesh.background_map.shape == img.shape
    none = prepare_image(img, zodi=zodi, background="none")
    assert np.allclose(none.data, img)


def test_mesh_falls_back_on_tiny_cutouts():
    p = prepare_image(np.ones((10, 10)), background="mesh")
    assert any("constant median" in h for h in p.history)
    assert np.allclose(p.data, 0)


def test_alignment_summary():
    rows = [{"offsets_arcsec": [1.0, None], "offsets_radec_arcsec": [[1.0, 0.0], None]},
            {"offsets_arcsec": [1.1, 0.2], "offsets_radec_arcsec": [[1.1, 0.1], [0.2, 0.0]]},
            {"offsets_arcsec": [0.9, 0.1], "offsets_radec_arcsec": [[0.9, -0.1], [0.0, 0.1]]}]
    s = alignment_summary(rows, max_rms_arcsec=2.0)
    assert s[0]["n_matched"] == 3 and s[1]["n_matched"] == 2
    assert s[0]["mean_offset_radec_arcsec"][0] == pytest.approx(1.0)
    assert s[0]["systematic_offset"] is True  # all three stars shifted east by 1"
    assert s[0]["max_arcsec"] == pytest.approx(np.hypot(1.1, 0.1))
    assert s[1]["systematic_offset"] is False


def _obs(oid, wl, det=6):
    z = np.zeros((3, 3))
    w, _ = make_reference_wcs(150.0, 10.0, 0.005)
    return ProcessedObservation(observation_id=oid, observation_time=None, mjd=60900.0, detector=det,
                                wavelength=wl, bandwidth=None, data=z, variance=None, mask=z.astype(np.uint8),
                                flags=z.astype(np.int64), raw=z, wavelength_map=None, wcs=w)


def test_stacks_split_mixed_wavelengths():
    same = [_obs("a", 4.50), _obs("b", 4.505)]
    mixed = [_obs("c", 4.0), _obs("d", 4.8)]
    out = split_by_wavelength({"epoch0_D6": same, "epoch1_D6": mixed})
    assert out["epoch0_D6"] == same
    assert "epoch1_D6" not in out and len([k for k in out if k.startswith("epoch1_D6_ch")]) == 2
