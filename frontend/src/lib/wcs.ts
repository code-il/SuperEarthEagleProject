/**
 * Gnomonic (TAN) world <-> pixel transform from FITS keywords.
 *
 * Matches astropy for the grids the processing engine produces (TAN, PC/CDELT or
 * CD matrix, LONPOLE 180, no distortion). Pixel coordinates here are 0-based
 * (FITS pixel 1 = index 0), with y increasing *up* (north on a north-up grid).
 */
import type { FitsWcs } from "../types";

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

export interface Tan {
  crpix: [number, number];
  crval: [number, number];
  /** Linear matrix (deg/pixel), [[m11, m12],[m21, m22]]. */
  m: [[number, number], [number, number]];
  inv: [[number, number], [number, number]];
}

export function makeTan(w: FitsWcs): Tan {
  const t1 = String(w.CTYPE1 ?? ""), t2 = String(w.CTYPE2 ?? "");
  if (!t1.includes("TAN") || !t2.includes("TAN")) throw new Error(`Only TAN projections are supported (got ${t1}, ${t2})`);
  let m: [[number, number], [number, number]];
  if (w.CD1_1 !== undefined) {
    m = [[Number(w.CD1_1), Number(w.CD1_2 ?? 0)], [Number(w.CD2_1 ?? 0), Number(w.CD2_2)]];
  } else {
    const c1 = Number(w.CDELT1 ?? 1), c2 = Number(w.CDELT2 ?? 1);
    m = [[c1 * Number(w.PC1_1 ?? 1), c1 * Number(w.PC1_2 ?? 0)], [c2 * Number(w.PC2_1 ?? 0), c2 * Number(w.PC2_2 ?? 1)]];
  }
  const det = m[0][0] * m[1][1] - m[0][1] * m[1][0];
  if (!det) throw new Error("Singular WCS matrix");
  const inv: [[number, number], [number, number]] = [[m[1][1] / det, -m[0][1] / det], [-m[1][0] / det, m[0][0] / det]];
  return { crpix: [Number(w.CRPIX1), Number(w.CRPIX2)], crval: [Number(w.CRVAL1), Number(w.CRVAL2)], m, inv };
}

/** 0-based pixel (x, y) -> (ra, dec) in degrees. */
export function pix2world(t: Tan, x: number, y: number): [number, number] {
  const dx = x + 1 - t.crpix[0], dy = y + 1 - t.crpix[1];
  const xi = (t.m[0][0] * dx + t.m[0][1] * dy) * D2R;
  const eta = (t.m[1][0] * dx + t.m[1][1] * dy) * D2R;
  const a0 = t.crval[0] * D2R, d0 = t.crval[1] * D2R;
  const den = Math.cos(d0) - eta * Math.sin(d0);
  const ra = a0 + Math.atan2(xi, den);
  const dec = Math.atan2(Math.sin(d0) + eta * Math.cos(d0), Math.hypot(xi, den));
  return [(((ra * R2D) % 360) + 360) % 360, dec * R2D];
}

/** (ra, dec) degrees -> 0-based pixel (x, y). Returns null for the far hemisphere. */
export function world2pix(t: Tan, ra: number, dec: number): [number, number] | null {
  const a = ra * D2R, d = dec * D2R, a0 = t.crval[0] * D2R, d0 = t.crval[1] * D2R;
  const cosc = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(a - a0);
  if (cosc <= 0) return null;
  const xi = (Math.cos(d) * Math.sin(a - a0)) / cosc * R2D;
  const eta = (Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(a - a0)) / cosc * R2D;
  const dx = t.inv[0][0] * xi + t.inv[0][1] * eta;
  const dy = t.inv[1][0] * xi + t.inv[1][1] * eta;
  return [dx + t.crpix[0] - 1, dy + t.crpix[1] - 1];
}

/** Pixel scale in arcsec (geometric mean of the two axes). */
export function pixelScaleArcsec(t: Tan): number {
  const det = Math.abs(t.m[0][0] * t.m[1][1] - t.m[0][1] * t.m[1][0]);
  return Math.sqrt(det) * 3600;
}

/** Position angle of north on the image (radians, from +y towards -x, i.e. counter-clockwise). */
export function northAngle(t: Tan): number {
  // Direction of increasing dec in pixel space.
  const [ra, dec] = pix2world(t, t.crpix[0] - 1, t.crpix[1] - 1);
  const p = world2pix(t, ra, Math.min(dec + 0.001, 89.999));
  if (!p) return 0;
  return Math.atan2(-(p[0] - (t.crpix[0] - 1)), p[1] - (t.crpix[1] - 1));
}

/** Angular separation in arcsec. */
export function separationArcsec(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const a1 = ra1 * D2R, d1 = dec1 * D2R, a2 = ra2 * D2R, d2 = dec2 * D2R;
  const s = Math.sin((d2 - d1) / 2) ** 2 + Math.cos(d1) * Math.cos(d2) * Math.sin((a2 - a1) / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(s))) * R2D * 3600;
}
