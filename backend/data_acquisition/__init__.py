"""SPHEREx data acquisition engine (Workstream 1).

Public interface::

    from backend.data_acquisition import (
        search_observations, get_observation_metadata, download_observation,
        download_cutout, download_cutouts, get_available_observation_dates,
        get_available_bands, resolve_target, resolve_name, parse_coordinates,
    )
"""
from .archive import (ArchiveError, get_available_bands, get_available_observation_dates,
                      get_observation_metadata, group_epochs, search_observations)
from .config import Release, Settings, get_settings, set_settings
from .coords import (MANDATORY_TARGETS, CoordinateError, ResolvedTarget, parse_coordinate_text,
                     parse_coordinates, predicted_positions, resolve_name, resolve_target,
                     validate_fov)
from .download import (BatchResult, DownloadError, build_cutout_url, download_cutout,
                       download_cutouts, download_observation)
from .fits_meta import decode_flag_definitions, extract_fits_metadata
from .http import HttpError
from .models import ObservationRecord

__all__ = [
    "ArchiveError", "BatchResult", "CoordinateError", "DownloadError", "HttpError",
    "MANDATORY_TARGETS", "ObservationRecord", "Release", "ResolvedTarget", "Settings",
    "build_cutout_url", "decode_flag_definitions", "download_cutout", "download_cutouts",
    "download_observation", "extract_fits_metadata", "get_available_bands",
    "get_available_observation_dates", "get_observation_metadata", "get_settings",
    "group_epochs", "parse_coordinate_text", "parse_coordinates", "predicted_positions",
    "resolve_name", "resolve_target", "search_observations", "set_settings", "validate_fov",
]
