import numpy as np
import pytest

from backend.image_processing import (MASK_PROFILES, divide_images, generate_display_image,
                                      load_spherex_fits, make_reference_wcs, prepare_image,
                                      reproject_observation, stack_observations, subtract_images)
from backend.image_processing.align import centroid, cutout_size_for_grid, reproject_bitmask, select_reference
from backend.image_processing.channels import (CHANNELS_PER_DETECTOR, all_channels, channel_for,
                                               channel_width_um)
from backend.image_processing.combine import convolve_nan, gaussian_kernel_sigma_px, psf_match
from backend.image_processing.display import (apply_stretch, compute_limits, make_animation,
                                              to_png_bytes)
from backend.image_processing.prepare import (MASK_BAD_VARIANCE, MASK_FLAGGED, MASK_NONFINITE,
                                              estimate_background, flags_to_mask)
from tests.processing_fixtures import make_frame, write_frame

RA, DEC = 150.0, 10.0


# ---------------------------------------------------------------- io
def test_load_spherex_fits(tmp_path):
    p = write_frame(tmp_path / "f.fits", stars=[(RA, DEC, 2.0)], nan_pixels=[(0, 0)])
    im = load_spherex_fits(p)
    assert im.image.dtype == np.float64 and im.image.shape == (41, 41)
    assert np.isnan(im.image[0, 0])
    assert im.variance is not None and im.zodi is not None and im.flags.dtype == np.int64
    assert im.flag_definitions["HOT"]["value"] == 1024
    assert im.meta["detector"] == 6 and im.meta["psf_fwhm_arcsec"] == 9.0
    assert im.meta["source_path"] == str(p)
    assert im.wavelength is None  # no WCS-WAVE in synthetic frame


# ---------------------------------------------------------------- prepare
def _flags(n=41):
    f = np.zeros((n, n), np.int64)
    f[5, 5] = 1024      # HOT       -> minimal+
    f[6, 6] = 2         # OVERFLOW  -> standard+
    f[7, 7] = 1         # TRANSIENT -> strict only
    f[8, 8] = 2097152   # SOURCE    -> never (informational)
    return f


def test_mask_profiles(tmp_path):
    im = load_spherex_fits(write_frame(tmp_path / "f.fits", flags=_flags()))
    counts = {}
    for prof in ("none", "minimal", "standard", "strict"):
        m, names = flags_to_mask(im.flags, im.flag_definitions, prof)
        counts[prof] = int(m.sum())
        assert "SOURCE" not in names and "FULLSAMPLE" not in names
    assert counts == {"none": 0, "minimal": 1, "standard": 2, "strict": 3}
    m, names = flags_to_mask(im.flags, im.flag_definitions, ["TRANSIENT"])
    assert names == ["TRANSIENT"] and m[7, 7] and m.sum() == 1
    with pytest.raises(ValueError):
        flags_to_mask(im.flags, im.flag_definitions, "bogus")
    with pytest.raises(ValueError):
        flags_to_mask(im.flags, im.flag_definitions, ["NOT_A_FLAG"])
    assert set(MASK_PROFILES) == {"none", "minimal", "standard", "strict"}


def test_prepare_image_background_and_masks(tmp_path):
    im = load_spherex_fits(write_frame(tmp_path / "f.fits", flags=_flags(), nan_pixels=[(1, 1)],
                                       stars=[(RA, DEC, 3.0)], zodi=0.5, sky=0.05))
    before = im.image.copy()
    var = im.variance.copy()
    var[2, 2] = -1
    prep = prepare_image(im.image, var, im.flags, flag_definitions=im.flag_definitions, zodi=im.zodi)
    assert np.array_equal(im.image, before, equal_nan=True)  # input untouched
    assert np.array_equal(prep.original, before, equal_nan=True)
    assert prep.mask[1, 1] & MASK_NONFINITE
    assert prep.mask[2, 2] & MASK_BAD_VARIANCE
    assert prep.mask[5, 5] & MASK_FLAGGED and prep.mask[6, 6] & MASK_FLAGGED
    assert prep.mask[7, 7] == 0  # TRANSIENT not in standard
    assert np.isnan(prep.data[5, 5]) and np.isnan(prep.variance[2, 2])
    assert prep.zodi_median == pytest.approx(0.5)
    assert prep.background_level == pytest.approx(0.05, abs=0.003)
    sky = np.nanmedian(prep.data[prep.valid])
    assert abs(sky) < 0.002  # background removed
    assert any("ZODI" in h for h in prep.history)
    raw = prepare_image(im.image, background="none", mask_profile="none")
    assert np.allclose(raw.data[np.isfinite(raw.data)], before[np.isfinite(before)])
    with pytest.raises(ValueError):
        prepare_image(im.image, background="nope")


def test_prepare_uncertainty_as_sigma():
    img = np.ones((5, 5))
    p = prepare_image(img, np.full((5, 5), 0.1), uncertainty_is_variance=False, background="none")
    assert np.allclose(p.variance, 0.01)


def test_estimate_background_robust_to_star():
    rng = np.random.default_rng(1)
    d = rng.normal(0.3, 0.01, (40, 40))
    d[18:22, 18:22] += 50
    assert estimate_background(d, np.ones_like(d, bool)) == pytest.approx(0.3, abs=0.005)
    assert estimate_background(d, np.zeros_like(d, bool)) is None


# ---------------------------------------------------------------- align
def test_reference_grid_north_up_and_centred():
    w, shape = make_reference_wcs(RA, DEC, 0.05)
    assert shape[0] == shape[1] and shape[0] % 2 == 1
    c = (shape[0] - 1) / 2
    ra, dec = w.pixel_to_world_values(c, c)
    assert ra == pytest.approx(RA) and dec == pytest.approx(DEC)
    r1, d1 = w.pixel_to_world_values(c, c + 1)
    r2, _ = w.pixel_to_world_values(c + 1, c)
    assert d1 > DEC  # +y = north
    assert r2 < RA   # +x = west (east left)
    assert cutout_size_for_grid(0.05) > 0.05 * 1.41
    with pytest.raises(ValueError):
        make_reference_wcs(RA, DEC, 0.05, 0)


@pytest.mark.parametrize("rot", [0.0, 37.0, -161.4, 152.5])
def test_reprojection_puts_star_at_true_position_and_keeps_flux(tmp_path, rot):
    star = (RA + 0.004, DEC - 0.003, 5.0)
    im = load_spherex_fits(write_frame(tmp_path / "f.fits", rot_deg=rot, stars=[star], noise=1e-4, zodi=0, sky=0))
    ref, shape = make_reference_wcs(RA, DEC, 0.05)
    out, fp = reproject_observation(im.image, im.wcs, ref, shape)
    x, y = ref.world_to_pixel_values(star[0], star[1])
    c = centroid(out, float(x), float(y), 4)
    assert np.hypot(c[0] - x, c[1] - y) < 0.1  # < 0.1 px (0.6")
    # surface-brightness integral conserved (same pixel area)
    xs, ys = im.wcs.world_to_pixel_values(star[0], star[1])
    yy, xx = np.indices(im.image.shape)
    native = im.image[np.hypot(xx - xs, yy - ys) <= 5].sum()
    yy, xx = np.indices(out.shape)
    aligned = np.nansum(out[np.hypot(xx - x, yy - y) <= 5])
    assert aligned / native == pytest.approx(1.0, abs=0.02)
    assert fp.max() == 1.0


def test_reprojection_exact_method(tmp_path):
    im = load_spherex_fits(write_frame(tmp_path / "f.fits", rot_deg=30, stars=[(RA, DEC, 5.0)], noise=1e-4))
    ref, shape = make_reference_wcs(RA, DEC, 0.04)
    out, fp = reproject_observation(im.image, im.wcs, ref, shape, "exact")
    c = (shape[0] - 1) / 2
    cc = centroid(out - np.nanmedian(out), c, c, 4)
    assert np.hypot(cc[0] - c, cc[1] - c) < 0.15
    with pytest.raises(ValueError):
        reproject_observation(im.image, im.wcs, ref, shape, "magic")


def test_footprint_marks_uncovered_area(tmp_path):
    # small frame, big grid -> edges uncovered
    im = load_spherex_fits(write_frame(tmp_path / "f.fits", n=11, rot_deg=45))
    ref, shape = make_reference_wcs(RA, DEC, 0.1)
    out, fp = reproject_observation(im.image, im.wcs, ref, shape)
    assert (fp == 0).any() and np.isnan(out[fp == 0]).all()


def test_reproject_bitmask_keeps_exact_bits(tmp_path):
    im = load_spherex_fits(write_frame(tmp_path / "f.fits", rot_deg=20, flags=_flags()))
    ref, shape = make_reference_wcs(RA, DEC, 0.06)
    out = reproject_bitmask(im.flags, im.wcs, ref, shape)
    assert set(np.unique(out)) <= {0, 1, 2, 1024, 2097152}


def test_select_reference():
    assert select_reference([0.2, 0.9, 0.5]) == 1
    assert select_reference([0.2, 0.9], "first") == 0
    assert select_reference([0.2, 0.9], 1) == 1
    with pytest.raises(ValueError):
        select_reference([])
    with pytest.raises(ValueError):
        select_reference([0.1], 5)


# ---------------------------------------------------------------- combine
def test_stack_methods():
    a = np.array([[1.0, 2.0], [np.nan, 4.0]])
    b = np.array([[3.0, 2.0], [5.0, np.nan]])
    c = np.array([[100.0, 2.0], [7.0, 8.0]])
    v = [np.full((2, 2), 1.0), np.full((2, 2), 1.0), np.full((2, 2), 4.0)]
    mean = stack_observations([a, b, c], "mean", v)
    assert mean.data[0, 0] == pytest.approx(104 / 3) and mean.data[1, 0] == pytest.approx(6)
    assert mean.variance[0, 0] == pytest.approx(6 / 9)
    assert list(mean.n_used.ravel()) == [3, 3, 2, 2]
    med = stack_observations([a, b, c], "median")
    assert med.data[0, 0] == 3.0  # outlier rejected
    w = stack_observations([a, b, c], "weighted", v)
    assert w.data[0, 0] == pytest.approx((1 + 3 + 100 / 4) / (1 + 1 + 0.25))
    assert w.variance[0, 0] == pytest.approx(1 / 2.25)
    mf = stack_observations([a, b, c], "mean", min_frames=3)
    assert np.isnan(mf.data[1, 0]) and np.isfinite(mf.data[0, 0])
    with pytest.raises(ValueError):
        stack_observations([a, np.zeros((3, 3))])
    with pytest.raises(ValueError):
        stack_observations([a, b], "weighted")
    with pytest.raises(ValueError):
        stack_observations([a], "mode")
    with pytest.raises(ValueError):
        stack_observations([])


def test_subtract_images():
    a = np.array([[5.0, 1.0], [np.nan, 2.0]])
    b = np.array([[1.0, 1.0], [1.0, 1.0]])
    r = subtract_images(a, b, np.full((2, 2), 1.0), np.full((2, 2), 3.0))
    assert r.data[0, 0] == 4 and np.isnan(r.data[1, 0]) and not r.valid[1, 0]
    assert r.variance[0, 0] == 4 and r.significance[0, 0] == pytest.approx(2.0)
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.01, (30, 30))
    off = subtract_images(x + 0.7, x * 0, match_background=True)
    assert abs(np.nanmedian(off.data)) < 0.005
    with pytest.raises(ValueError):
        subtract_images(a, np.zeros((3, 3)))


def test_divide_images():
    a = np.array([[10.0, 1.0], [4.0, 4.0]])
    b = np.array([[5.0, 0.1], [0.0, 2.0]])
    r = divide_images(a, b, np.full((2, 2), 0.25), np.full((2, 2), 0.25), min_snr_b=5)
    assert r.data[0, 0] == pytest.approx(2.0)
    assert np.isnan(r.data[0, 1])  # B S/N = 0.2 < 5
    assert np.isnan(r.data[1, 0])  # B = 0
    assert np.isnan(r.data[1, 1])  # B S/N = 4 < 5
    assert r.variance[0, 0] == pytest.approx(4 * (0.25 / 100 + 0.25 / 25))
    r2 = divide_images(a, b, min_abs_b=1.0)
    assert r2.data[1, 1] == 2.0 and np.isnan(r2.data[0, 1])


def test_psf_matching():
    assert gaussian_kernel_sigma_px(7.5, 9.0, 6.15) == pytest.approx(np.sqrt(81 - 56.25) / 2.3548 / 6.15)
    assert gaussian_kernel_sigma_px(9, 7.5, 6.15) == 0
    img = np.zeros((21, 21))
    img[10, 10] = 1.0
    a, va, b, vb, h = psf_match(img, 5.0, img.copy(), 12.0, 6.15)
    assert a[10, 10] < 1 and b[10, 10] == 1 and "blurred A" in h[0]
    assert np.nansum(a) == pytest.approx(1.0, abs=0.01)
    _, _, _, _, h2 = psf_match(img, None, img, 9.0, 6.15)
    assert "skipped" in h2[0]
    img[0, 0] = np.nan
    out, _ = convolve_nan(img, 1.0)
    assert np.isnan(out[0, 0]) and np.isfinite(out[10, 10])


# ---------------------------------------------------------------- display
def test_display_rendering(tmp_path):
    img = np.arange(16, dtype=float).reshape(4, 4)
    img[0, 0] = np.nan
    rgba = generate_display_image(img, stretch="linear", vmin=0, vmax=15)
    assert rgba.shape == (4, 4, 4) and rgba.dtype == np.uint8
    assert rgba[-1, 0, 3] == 0  # NaN (row 0) is transparent and flipped to the bottom
    assert rgba[0, 3, 0] == 255  # max value (row 3, col 3) at top-right
    div = generate_display_image(np.array([[-1.0, 0.0, 1.0]]), diverging=True, flip_y=False)
    assert div[0, 2, 0] > div[0, 2, 2]  # positive -> red
    assert div[0, 0, 2] > div[0, 0, 0]  # negative -> blue
    assert tuple(div[0, 1, :3]) == (255, 255, 255)
    for s in ("linear", "sqrt", "log", "asinh"):
        y = apply_stretch(np.linspace(0, 1, 11), s)
        assert y[0] == pytest.approx(0) and y[-1] == pytest.approx(1) and np.all(np.diff(y) >= 0)
    with pytest.raises(ValueError):
        apply_stretch(np.zeros(3), "nope")
    lo, hi = compute_limits([np.array([0.0, 1.0]), np.array([np.nan, 10.0])], (0, 100))
    assert (lo, hi) == (0.0, 10.0)
    assert compute_limits(np.array([-3.0, 1.0]), (0, 100), symmetric=True) == (-3.0, 3.0)
    assert to_png_bytes(rgba)[:8] == b"\x89PNG\r\n\x1a\n"
    gif = make_animation([rgba, rgba], tmp_path / "a.gif")
    assert gif.read_bytes()[:6] in (b"GIF89a", b"GIF87a")


# ---------------------------------------------------------------- channels
def test_channels():
    ch = all_channels()
    assert len(ch) == 102 and ch[0].detector == 1 and ch[-1].detector == 6
    assert CHANNELS_PER_DETECTOR == 17
    c = channel_for(6, 4.923)
    assert c.detector == 6 and c.wavelength_min <= 4.923 < c.wavelength_max
    assert channel_for(6, 9.0).local_index == 16  # clamped
    assert channel_for(None, 1.0) is None and channel_for(3, None) is None
    assert 0.02 < channel_width_um(6, 4.7) < 0.05
    # resolving power roughly constant within a detector (log spacing)
    r = [x.wavelength_center / (x.wavelength_max - x.wavelength_min) for x in ch if x.detector == 3]
    assert max(r) / min(r) == pytest.approx(1.0, abs=1e-6)
