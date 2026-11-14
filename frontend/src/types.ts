/**
 * Data contracts between the viewer and the other workstreams.
 *
 * Person 1 (data acquisition)  -> SearchResponse / ObservationRecord   (GET /api/observations)
 * Person 2 (image processing)  -> ProcessResponse / ProcessedObservation, CompareResponse, StackProduct
 * Person 4 (analysis)          -> SourceMarker, LightCurve, Spectrum   (optional; see README "Integration")
 * Catalogue overlays           -> CatalogResponse                     (GET /api/catalog/{gaia|catwise})
 *
 * Field names mirror the backend JSON exactly (snake_case) so no mapping layer is needed.
 */

/** Celestial WCS as FITS keywords (TAN projection, from astropy `wcs.to_header()`). */
export interface FitsWcs {
  CTYPE1: string;
  CTYPE2: string;
  CRPIX1: number;
  CRPIX2: number;
  CRVAL1: number;
  CRVAL2: number;
  CDELT1?: number;
  CDELT2?: number;
  PC1_1?: number;
  PC1_2?: number;
  PC2_1?: number;
  PC2_2?: number;
  CD1_1?: number;
  CD1_2?: number;
  CD2_1?: number;
  CD2_2?: number;
  [key: string]: string | number | boolean | undefined;
}

export interface Channel {
  index: number;
  detector: number;
  local_index?: number;
  label: string;
  wavelength_min: number;
  wavelength_max: number;
  wavelength_center?: number;
}

export interface ResolvedTarget {
  query: string;
  name: string | null;
  ra: number;
  dec: number;
  ref_epoch: string | null;
  pm_ra_cosdec_mas_yr: number | null;
  pm_dec_mas_yr: number | null;
  parallax_mas: number | null;
  radial_velocity_km_s?: number | null;
  object_type?: string | null;
  source?: string | null;
  note?: string | null;
}

/** How the query position was understood by the backend. */
export interface QueryPosition {
  query: string;
  ra: number;
  dec: number;
  kind: "coordinates" | "name" | string;
  name: string | null;
  epoch: string | null;
  target: ResolvedTarget | null;
}

/** Person 1 contract: one SPHEREx image (detector) that covers the field. */
export interface ObservationRecord {
  observation_id: string;
  observation_time: string;
  ra: number;
  dec: number;
  wavelength_min: number;
  wavelength_max: number;
  band: string;
  detector: number;
  data_release: string;
  calibration_level?: number;
  quality_flags: string[];
  fits_url: string;
  local_path?: string | null;
  mjd_start?: number;
  exposure_time_s?: number;
  covers_position?: boolean;
  wavelength_at_target?: number | null;
  /** Present when the query was a named target with proper motion. */
  target_predicted_ra?: number;
  target_predicted_dec?: number;
  [key: string]: unknown;
}

/** A visit cluster (SPHEREx revisits a field roughly every 6 months). */
export interface Epoch {
  epoch_index: number;
  start: string;
  end: string;
  mjd_start: number;
  mjd_end: number;
  mjd_mean: number;
  n_observations: number;
  n_per_detector: Record<string, number>;
  releases: string[];
}

export interface SearchResponse {
  query: QueryPosition;
  count: number;
  observations: ObservationRecord[];
  epochs: Epoch[];
  [key: string]: unknown;
}

/** Person 2 contract: one observation reprojected onto the common north-up grid. */
export interface ProcessedObservation {
  observation_id: string;
  observation_time: string;
  wavelength: number;
  image_url: string;
  fits_url: string;
  wcs: FitsWcs;
  quality_flags: string[];
  processing_method: string;
  mjd: number;
  detector: number;
  bandwidth: number | null;
  channel: Channel | null;
  data_release: string;
  valid_fraction: number;
  background_level: number | null;
  zodi_median: number | null;
  psf_fwhm_arcsec: number | null;
  masked_flags: string[];
  display_limits: [number, number] | null;
  units: string;
  /** "CWAVE/CBAND" (science calibration) or "WCS-WAVE (approximate lookup)". */
  wavelength_source?: string | null;
  wavelength_calibration?: string | null;
  pipeline_version?: string | null;
  history: string[];
}

export interface Grid {
  center_ra: number;
  center_dec: number;
  fov_deg: number;
  shape: [number, number];
  pixel_scale_arcsec: number;
  orientation: string;
  wcs: FitsWcs;
}

export interface StackProduct {
  key: string;
  group_by: string;
  method: string;
  n_frames: number;
  observation_ids: string[];
  detectors: number[];
  time_start: string;
  time_end: string;
  mjd_mean: number;
  wavelength_mean: number;
  wavelength_min: number;
  wavelength_max: number;
  warnings: string[];
  image_url: string;
  fits_url: string;
  valid_fraction: number;
  display_limits: [number, number] | null;
  wcs: FitsWcs;
}

export interface ProcessResponse {
  grid: Grid;
  count: number;
  observations: ProcessedObservation[];
  animations: Record<string, string>;
  errors: { observation_id?: string; error: string; [k: string]: unknown }[];
  skipped: unknown[];
  query: QueryPosition;
  stacks?: StackProduct[];
}

export interface CompareSelector {
  observation_ids?: string[];
  epoch_index?: number;
  date_start?: string;
  date_end?: string;
  wavelength_min?: number;
  wavelength_max?: number;
}

export interface CompareResponse {
  mode: "difference" | "ratio";
  name: string;
  image_url: string;
  significance_url: string | null;
  fits_url: string;
  a: { observation_ids: string[]; [k: string]: unknown };
  b: { observation_ids: string[]; [k: string]: unknown };
  stats: Record<string, number>;
  caveats: string[];
  history: string[];
  wcs: FitsWcs;
  display: string;
  grid?: Grid;
}

export interface CatalogSource {
  id: string;
  ra: number;
  dec: number;
  ref_mjd: number | null;
  pm_ra_cosdec_mas_yr: number | null;
  pm_dec_mas_yr: number | null;
  parallax_mas: number | null;
  mag: number | null;
  mag_band: string;
  /** CatWISE artefact flags (cc_flags), when present. */
  cc_flags?: string | null;
  /** Large proper motion that is probably spurious (next to a saturated star or flagged): not applied. */
  pm_suspect?: boolean;
}

export interface CatalogResponse {
  catalog: "gaia" | "catwise";
  center_ra: number;
  center_dec: number;
  radius_deg: number;
  count: number;
  sources: CatalogSource[];
}

/**
 * Person 4 contract: a source / candidate to mark on the image.
 * Give `ra`/`dec` at `mjd`; if proper motion is known the viewer moves the marker
 * to each frame's date. `positions` (per-observation) overrides propagation.
 */
export interface SourceMarker {
  id: string;
  label?: string;
  ra: number;
  dec: number;
  mjd?: number;
  pm_ra_cosdec_mas_yr?: number;
  pm_dec_mas_yr?: number;
  positions?: { observation_id: string; ra: number; dec: number }[];
  kind?: "candidate" | "known" | "artifact" | string;
  score?: number;
  notes?: string;
}

/** Person 4 contract: calibrated photometry for the bottom panel. */
export interface LightCurve {
  source_id: string;
  label?: string;
  points: { mjd: number; flux: number; flux_err?: number; wavelength?: number; observation_id?: string }[];
  units: string;
}

/** Person 4: one compact record per source (GET /api/analysis -> results_summary). */
export interface SourceSummary {
  source_id: string;
  ra: number;
  dec: number;
  ref_mjd?: number | null;
  classification: string;
  label: string;
  confidence: number;
  status: "known" | "candidate" | string;
  subtype?: string | null;
  note?: string | null;
  is_target: boolean;
  is_field_centre: boolean;
  saturated: boolean;
  artefact_risk: boolean;
  secondary_flags: string[];
  n_visits?: number | null;
  n_frame_detections?: number | null;
  angular_motion?: {
    model?: string; baseline_yr?: number; pm_ra_cosdec_mas_yr?: number; pm_dec_mas_yr?: number; pm_total_mas_yr?: number;
    pm_ra_cosdec_err_mas_yr?: number; pm_dec_err_mas_yr?: number; pm_err_mas_yr?: number; pm_pa_deg?: number; pm_snr?: number;
    motion_sigma?: number; linear_chi2_red?: number | null; displacement_arcsec?: number; parallax_removed_mas?: number | null;
    position_err_at_ref_arcsec?: number; ra_at_ref?: number; dec_at_ref?: number;
  } | null;
  variability_indicator?: {
    sigma?: number | null; p_value?: number | null; amplitude?: number | null; amplitude_mag?: number | null;
    reliable?: boolean; significant?: boolean; n_visits?: number; reason?: string | null; release_note?: string | null; warning?: string | null;
  } | null;
  known_catalogue_match?: {
    catalog: string; id: string; separation_arcsec: number; pm_ra_cosdec_mas_yr?: number | null; pm_dec_mas_yr?: number | null;
    pm_difference_sigma?: number | null; pm_consistent?: boolean | null; mag?: number | null; mag_band?: string | null; otype?: string | null;
  } | null;
  criteria: { name: string; value: number | null; threshold: number; strong?: number; unit?: string; passed: boolean; score?: number; required?: boolean }[];
  has_light_curve: boolean;
  has_spectrum: boolean;
}

/** GET /api/analysis (full=false). */
export interface AnalysisResponse {
  sources: SourceMarker[];
  light_curves: LightCurve[];
  spectra: Spectrum[];
  results_summary?: SourceSummary[];
  analysis_version?: string;
  summary?: { n_observations: number; n_visits: number; n_sources: number; classes: Record<string, number>; runtime_s?: number };
  target?: { name?: string; source_id?: string | null; median_offset_from_prediction_arcsec?: number | null; note?: string; catalogue_pm_mas_yr?: [number, number] | null } | null;
  warnings?: string[];
  disclaimer?: string;
  registration_calibration?: { method: string; sigma_arcsec: number; n_reference_sources?: number; reason?: string };
  provenance?: { releases?: string[]; wavelength_source?: string[]; pipeline_versions?: string[]; [k: string]: unknown };
  catalogues?: Record<string, { status?: string; count?: number; [k: string]: unknown }>;
}

export interface Spectrum {
  source_id: string;
  label?: string;
  points: { wavelength: number; flux: number; flux_err?: number }[];
  units: string;
}

// ---------------------------------------------------------------------------
// Viewer-side state
// ---------------------------------------------------------------------------

export type Stretch = "linear" | "sqrt" | "log" | "asinh";
export type Colormap = "gray" | "inferno" | "magma" | "viridis" | "diverging";
export type ViewMode = "single" | "blink" | "side-by-side" | "difference" | "ratio" | "stacked";
export type LimitsMode = "series" | "frame";

export interface DisplaySettings {
  stretch: Stretch;
  colormap: Colormap;
  invert: boolean;
  /** -1..1, shifts the normalised value. */
  brightness: number;
  /** 0.2..5, scales around mid-grey. */
  contrast: number;
  /** Lower / upper percentile for automatic limits. */
  percentiles: [number, number];
  /** "series": shared limits per detector (true brightness changes stay visible). */
  limitsMode: LimitsMode;
}

export interface OverlaySettings {
  target: boolean;
  gaia: boolean;
  catwise: boolean;
  sources: boolean;
  labels: boolean;
  compass: boolean;
  /** Gaia G / CatWISE W2 magnitude limit for drawn markers. */
  maxMag: number;
}

/** A decoded image plane ready to draw. */
export interface ImagePlane {
  width: number;
  height: number;
  /** Row-major, FITS order: row 0 is the BOTTOM of the image (south when north-up). */
  data: Float32Array;
  /** Non-zero = invalid pixel; optional. */
  mask?: Uint8Array;
  wcs: FitsWcs;
  units?: string;
}

/** A position to draw on the image, already at the frame's epoch. */
export interface Marker {
  id: string;
  ra: number;
  dec: number;
  label?: string;
  kind: "target" | "gaia" | "catwise" | "source";
  mag?: number | null;
  title?: string;
  /** Drawn dashed: position or motion of doubtful reliability. */
  suspect?: boolean;
  /** Analysis markers: "candidate" | "known" | "artifact" | "source" (SourceMarker.kind). */
  tone?: string;
  /** The source selected in the Analysis panel (drawn highlighted). */
  selected?: boolean;
}

export interface ViewState {
  /** Screen pixels per image pixel. */
  zoom: number;
  /** Image pixel (0-based, FITS x/y) at the canvas centre. */
  cx: number;
  cy: number;
}

export interface PixelReadout {
  x: number;
  y: number;
  ra: number;
  dec: number;
  value: number | null;
  masked: boolean;
}

/** One of the challenge's mandatory targets (GET /api/targets). */
export interface TargetInfo {
  key: string;
  label: string;
  query: string;
  why: string;
}
