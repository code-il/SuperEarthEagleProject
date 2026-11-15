"""Display rendering: stretches, colour maps and PNG/GIF output.

Display images are derived products only. They never feed back into the
science arrays. Invalid pixels are transparent (alpha = 0), so the viewer
can tell "no data" from "dark sky".
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Sequence

import numpy as np

STRETCHES = ("linear", "sqrt", "log", "asinh")


def compute_limits(images: np.ndarray | Sequence[np.ndarray], percentiles: tuple[float, float] = (0.5, 99.5),
                   symmetric: bool = False) -> tuple[float, float]:
    """Display limits from the valid pixels of one or several images.

    Use one set of limits for a whole time series so frames do not flicker.
    """
    arrs = [images] if isinstance(images, np.ndarray) else list(images)
    vals = np.concatenate([np.asarray(a, float)[np.isfinite(a)].ravel() for a in arrs] or [np.array([])])
    if vals.size == 0:
        return (-1.0, 1.0) if symmetric else (0.0, 1.0)
    if symmetric:
        m = float(np.percentile(np.abs(vals), percentiles[1]))
        m = m if m > 0 else 1.0
        return -m, m
    lo, hi = (float(x) for x in np.percentile(vals, percentiles))
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi


def apply_stretch(x: np.ndarray, stretch: str = "asinh", asinh_a: float = 0.1) -> np.ndarray:
    """Map values already normalised to [0, 1] through a stretch."""
    x = np.clip(x, 0, 1)
    if stretch == "linear":
        return x
    if stretch == "sqrt":
        return np.sqrt(x)
    if stretch == "log":
        return np.log10(1 + 1000 * x) / np.log10(1001)
    if stretch == "asinh":
        return np.arcsinh(x / asinh_a) / np.arcsinh(1 / asinh_a)
    raise ValueError(f"stretch must be one of {STRETCHES}")


def _diverging_lut() -> np.ndarray:
    """255-entry blue-white-red table (negative = blue, positive = red).

    Odd length so that zero maps exactly to the white centre entry (127).
    """
    t = np.linspace(-1, 1, 255)
    r = np.where(t < 0, 1 + t * 0.85, 1.0)
    g = 1 - np.abs(t) * 0.85
    b = np.where(t > 0, 1 - t * 0.85, 1.0)
    return (np.stack([r, g, b], axis=1) * 255).round().astype(np.uint8)


def generate_display_image(image: np.ndarray, stretch: str = "asinh", vmin: float | None = None,
                           vmax: float | None = None, percentiles: tuple[float, float] = (0.5, 99.5),
                           invert: bool = False, diverging: bool = False,
                           valid: np.ndarray | None = None, flip_y: bool = True) -> np.ndarray:
    """Render a science array as an RGBA uint8 image.

    * ``diverging=True``: symmetric limits around 0 with a blue-white-red map
      (for difference / significance images; ``stretch`` is ignored).
    * Otherwise greyscale with the chosen stretch.
    * ``flip_y``: FITS row 0 is the bottom; images are stored top row first,
      so rows are flipped (north up, east left for the default grid).
    """
    img = np.asarray(image, float)
    ok = np.isfinite(img) if valid is None else (np.asarray(valid, bool) & np.isfinite(img))
    if diverging:
        if vmin is None or vmax is None:
            vmin, vmax = compute_limits(np.where(ok, img, np.nan), percentiles, symmetric=True)
        m = max(abs(vmin), abs(vmax))
        idx = np.clip(((np.where(ok, img, 0) / m) + 1) / 2 * 254, 0, 254).round().astype(np.uint8)
        rgb = _diverging_lut()[idx]
    else:
        if vmin is None or vmax is None:
            lo, hi = compute_limits(np.where(ok, img, np.nan), percentiles)
            vmin = lo if vmin is None else vmin
            vmax = hi if vmax is None else vmax
        norm = (np.where(ok, img, vmin) - vmin) / ((vmax - vmin) or 1.0)
        g = apply_stretch(norm, stretch)
        if invert:
            g = 1 - g
        g8 = (g * 255).round().astype(np.uint8)
        rgb = np.stack([g8, g8, g8], axis=-1)
    alpha = np.where(ok, 255, 0).astype(np.uint8)
    rgba = np.concatenate([rgb, alpha[..., None]], axis=-1)
    return rgba[::-1] if flip_y else rgba


def upscale(rgba: np.ndarray, factor: int) -> np.ndarray:
    """Nearest-neighbour enlargement (keeps pixels honest: no interpolation)."""
    if factor <= 1:
        return rgba
    return np.repeat(np.repeat(rgba, factor, axis=0), factor, axis=1)


def to_png_bytes(rgba: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def save_png(rgba: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(to_png_bytes(rgba))
    return path


def make_animation(frames: Sequence[np.ndarray], path: str | Path, duration_ms: int = 500,
                   background: tuple[int, int, int] = (0, 0, 0)) -> Path:
    """Animated GIF (blink / time-lapse preview) from RGBA frames of equal size."""
    from PIL import Image

    if not frames:
        raise ValueError("No frames")
    imgs = []
    for f in frames:
        base = Image.new("RGB", (f.shape[1], f.shape[0]), background)
        base.paste(Image.fromarray(f, mode="RGBA"), mask=Image.fromarray(f[..., 3]))
        imgs.append(base.convert("P", palette=Image.ADAPTIVE))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=duration_ms, loop=0)
    return path
