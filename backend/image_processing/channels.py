"""SPHEREx spectral channels.

SPHEREx has six detector arrays, each behind a linear variable filter (LVF):
the wavelength changes smoothly across the array. The mission describes its
spectra as 102 channels: 17 per detector. Because of the LVF, the wavelength
a source is seen at depends on where it lands on the array, which changes
from exposure to exposure. So the *channel* of an observation is defined by
the wavelength at the target pixel (``wavelength_at_target``), not by the
detector alone.

The channel edges used here are a nominal approximation: 17 bins per
detector, equally spaced in log(wavelength) between the detector's archive
limits (``em_min``/``em_max``, measured from DR1 SIA2 records). Equal log
spacing is what a constant resolving power implies. These are for grouping
only. Flux calibration always uses each pixel's own wavelength from the
spectral WCS.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CHANNELS_PER_DETECTOR = 17

# Detector wavelength limits (um) from DR1 SIA2 em_min/em_max (verified 2026-11-14).
DETECTOR_RANGES_UM: dict[int, tuple[float, float]] = {
    1: (0.7455, 1.1167),
    2: (1.1033, 1.6499),
    3: (1.6301, 2.4346),
    4: (2.4027, 3.8473),
    5: (3.8113, 4.4300),
    6: (4.4115, 5.0096),
}


@dataclass(frozen=True)
class Channel:
    index: int
    """Global channel index 0..101."""
    detector: int
    local_index: int
    """0..16 within the detector."""
    wavelength_min: float
    wavelength_max: float

    @property
    def wavelength_center(self) -> float:
        return float(np.sqrt(self.wavelength_min * self.wavelength_max))

    @property
    def label(self) -> str:
        return f"D{self.detector}-{self.local_index:02d} ({self.wavelength_center:.3f} um)"


def channel_edges(detector: int) -> np.ndarray:
    lo, hi = DETECTOR_RANGES_UM[detector]
    return np.geomspace(lo, hi, CHANNELS_PER_DETECTOR + 1)


def all_channels() -> list[Channel]:
    out = []
    for det in range(1, 7):
        e = channel_edges(det)
        for i in range(CHANNELS_PER_DETECTOR):
            out.append(Channel((det - 1) * CHANNELS_PER_DETECTOR + i, det, i, float(e[i]), float(e[i + 1])))
    return out


def channel_for(detector: int | None, wavelength_um: float | None) -> Channel | None:
    """Nominal channel of an observation, from its detector and wavelength at the target."""
    if detector not in DETECTOR_RANGES_UM or wavelength_um is None or not np.isfinite(wavelength_um):
        return None
    e = channel_edges(int(detector))
    i = int(np.clip(np.searchsorted(e, wavelength_um, side="right") - 1, 0, CHANNELS_PER_DETECTOR - 1))
    return Channel((int(detector) - 1) * CHANNELS_PER_DETECTOR + i, int(detector), i, float(e[i]), float(e[i + 1]))


def channel_width_um(detector: int, wavelength_um: float) -> float:
    ch = channel_for(detector, wavelength_um)
    return (ch.wavelength_max - ch.wavelength_min) if ch else float("nan")
