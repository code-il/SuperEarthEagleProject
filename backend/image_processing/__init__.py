"""SPHEREx image processing engine (Workstream 2).

Turns SPHEREx L2 FITS (from the data acquisition module) into aligned,
cleaned images on a common sky grid, plus stacks, difference and ratio
images and display-ready PNG/GIF renderings.
"""
from .align import (NATIVE_PIXEL_SCALE_ARCSEC, check_alignment, cutout_size_for_grid,
                    make_reference_wcs, reproject_observation, select_reference)
from .channels import all_channels, channel_for
from .combine import (divide_images, psf_match, stack_observations, subtract_images)
from .display import compute_limits, generate_display_image, make_animation, save_png
from .io import SpherexImage, extract_science_image, load_spherex_fits
from .pipeline import (ProcessedObservation, SeriesResult, compare, group_observations,
                       load_processed_observation, make_stacks, match_wavelengths,
                       process_observation, process_series, save_processed_observation,
                       select_observations)
from .prepare import MASK_PROFILES, prepare_image

__all__ = [
    "MASK_PROFILES", "NATIVE_PIXEL_SCALE_ARCSEC", "ProcessedObservation", "SeriesResult",
    "SpherexImage", "all_channels", "channel_for", "check_alignment", "compare", "compute_limits",
    "cutout_size_for_grid", "divide_images", "extract_science_image", "generate_display_image",
    "group_observations", "load_processed_observation", "load_spherex_fits", "make_animation",
    "make_reference_wcs", "make_stacks", "match_wavelengths", "prepare_image", "process_observation",
    "process_series", "psf_match", "reproject_observation", "save_png", "save_processed_observation",
    "select_observations", "select_reference", "stack_observations", "subtract_images",
]
