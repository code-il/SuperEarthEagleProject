"""SPHEREx time-series analysis engine (Workstream 4).

Works on Person 2's processed observations (``backend.image_processing``):
finds sources, follows them through time, measures motion, light curves
and multi-channel spectra, checks reference catalogues, and assigns
preliminary, criteria-based classes. Nothing it outputs is a discovery
claim; see README "Scientific limitations".
"""
from .classify import classify_candidate
from .crossmatch import crossmatch_catalogue, field_catalogues, match_to_catalogue, query_skybot
from .detection import detect_sources, extract_source_photometry
from .matching import build_visit_stacks, estimate_registration, find_tracklets, link_detections, match_sources
from .motion import calculate_angular_motion, parallax_factors, predict_position
from .photometry import (compare_spectra, forced_photometry, generate_light_curve, generate_spectrum,
                         variability_indicator)
from .pipeline import (ANALYSIS_VERSION, analyse_field, analyse_observations, find_source, identify_moving_candidates,
                       summarise_source, to_viewer)

__all__ = [
    "ANALYSIS_VERSION", "analyse_field", "analyse_observations", "build_visit_stacks", "calculate_angular_motion",
    "classify_candidate", "compare_spectra", "crossmatch_catalogue", "detect_sources", "estimate_registration",
    "extract_source_photometry", "field_catalogues", "find_source", "find_tracklets", "forced_photometry", "generate_light_curve",
    "generate_spectrum", "identify_moving_candidates", "link_detections", "match_sources", "match_to_catalogue",
    "parallax_factors", "predict_position", "query_skybot", "summarise_source", "to_viewer", "variability_indicator",
]
