"""Shared data structures (the JSON integration contract)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ObservationRecord:
    """One SPHEREx spectral image (one exposure on one detector).

    The first block of fields is the integration contract agreed in the
    development prompt. The second block is additional, optional metadata.
    Any value the archive does not provide is ``None``; nothing is invented.

    Units: angles in degrees, wavelengths in micrometres, times in UTC.
    """

    observation_id: str
    """``<obs_id>_D<detector>``, e.g. ``2025W33_2C_0321_1_D6``. Unique per exposure+detector."""
    observation_time: str | None
    """Exposure start, ISO 8601 UTC (``...Z``)."""
    ra: float | None
    """Pointing centre of the full 2040x2040 image (not the search position)."""
    dec: float | None
    wavelength_min: float | None
    """Shortest wavelength covered anywhere on this detector (um)."""
    wavelength_max: float | None
    band: str | None
    """Archive band name, e.g. ``SPHEREx-D3``."""
    detector: int | None
    """Detector array 1-6 (1-3 short-wave, 4-6 mid-wave IR)."""
    data_release: str | None
    calibration_level: int | None
    quality_flags: list[str] = field(default_factory=list)
    """Problem flags at the target pixel; filled once a cutout has been downloaded."""
    fits_url: str | None = None
    local_path: str | None = None

    # ---- additional metadata ---------------------------------------------
    obs_id: str | None = None
    publisher_did: str | None = None
    collection: str | None = None
    observation_time_end: str | None = None
    mjd_start: float | None = None
    mjd_end: float | None = None
    exposure_time_s: float | None = None
    instrument: str | None = None
    pixel_scale_arcsec: float | None = None
    image_size_px: list[int] | None = None
    image_fov_deg: float | None = None
    spectral_resolving_power: float | None = None
    s_region: str | None = None
    covers_position: bool | None = None
    """True if the image footprint contains the search position itself (not just
    part of the search circle). Cutouts can only be made when this is True."""
    archive_release_date: str | None = None
    estimated_size_kb: int | None = None
    survey_field: str | None = None
    cloud_access: dict[str, Any] | None = None
    wavelength_at_target: float | None = None
    """Central wavelength (um) at the cutout centre, from the spectral WCS. Needs a download."""
    bandwidth_at_target: float | None = None
    quality: dict[str, Any] | None = None
    """Detailed quality info extracted from the downloaded FITS headers/FLAGS plane."""
    cutout: dict[str, Any] | None = None
    """Cutout provenance (centre, size, URL, sha256, ...) once downloaded."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ObservationRecord":
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in names})
