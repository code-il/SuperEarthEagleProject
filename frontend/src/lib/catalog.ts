import type { CatalogSource, SourceMarker } from "../types";

/** Linear proper-motion position at `mjd` (same model as backend/catalogs.py). */
export function propagate(
  src: { ra: number; dec: number; ref_mjd?: number | null; mjd?: number; pm_ra_cosdec_mas_yr?: number | null; pm_dec_mas_yr?: number | null; pm_suspect?: boolean },
  mjd: number,
): [number, number] {
  const ref = src.ref_mjd ?? src.mjd;
  const pa = src.pm_ra_cosdec_mas_yr, pd = src.pm_dec_mas_yr;
  if (ref == null || pa == null || pd == null || src.pm_suspect) return [src.ra, src.dec];
  const dt = (mjd - ref) / 365.25;
  const dec = src.dec + (pd * dt) / 3.6e6;
  const ra = src.ra + (pa * dt) / 3.6e6 / Math.max(Math.cos((src.dec * Math.PI) / 180), 1e-6);
  return [((ra % 360) + 360) % 360, dec];
}

export function catalogPosition(s: CatalogSource, mjd: number): [number, number] {
  return propagate(s, mjd);
}

/** Where a Person-4 source marker sits on a given observation. */
export function markerPosition(m: SourceMarker, observationId: string, mjd: number): [number, number] {
  const p = m.positions?.find((x) => x.observation_id === observationId);
  if (p) return [p.ra, p.dec];
  return propagate(m, mjd);
}

/** Total proper motion in mas/yr, or null. */
export function totalPm(s: { pm_ra_cosdec_mas_yr?: number | null; pm_dec_mas_yr?: number | null }): number | null {
  if (s.pm_ra_cosdec_mas_yr == null || s.pm_dec_mas_yr == null) return null;
  return Math.hypot(s.pm_ra_cosdec_mas_yr, s.pm_dec_mas_yr);
}
