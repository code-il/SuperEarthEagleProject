/**
 * Planet positions for the Atlas's system view.
 *
 * Model: each planet follows its own two-body Keplerian ellipse around the star (analytic,
 * no numerical integrator). Kepler's equation M = E − e·sin E is solved by Newton iteration
 * for every frame, so a planet moves faster near periastron, as it should. Planet–planet
 * gravity, relativity and tides are ignored, so this is a Keplerian *prediction*, not an
 * N-body simulation.
 *
 * Elements come from the NASA Exoplanet Archive (pscomppars): period P, semi-major axis a,
 * eccentricity e, argument of periastron ω, and an epoch that fixes where the planet is:
 *   - transit midpoint T₀ (transiting planets): at T₀ the planet is between the star and us;
 *   - time of periastron T_p (some radial-velocity planets);
 *   - neither: the phase is unknown, so no position is drawn (only the orbit).
 * The phase uncertainty grows with time from the epoch: σ_M = 2π/P · √(σ_T² + (n·σ_P)²),
 * with n = (t − T)/P orbits elapsed.
 *
 * Time runs at one fixed rate for every system: DAYS_PER_SECOND simulated days per real second
 * (1 day/s, so Mercury would take 88 s to go round). It is never sped up per system.
 *
 * Nothing is assumed silently. Each orbit records what was measured:
 *   - shape: "measured" (e and, if e > 0, omega from the archive), "circle" (the archive gives no
 *     eccentricity, or 0 with no uncertainty, i.e. fixed by the authors: drawn as a circle of
 *     radius a and labelled "shape not measured"), or "band" (e measured but omega not: the
 *     ellipse's orientation is unknown, so only the measured range of distances a(1-e)..a(1+e)
 *     is drawn, with no position);
 *   - inclination i to our line of sight (measured for most transiting planets). When it is
 *     missing the orbit is drawn edge-on and labelled.
 * The rotation of the orbit around our line of sight (longitude of the node) cannot be
 * measured from transits or radial velocities; it is the one free choice in the drawing
 * (line of nodes horizontal), and it changes nothing that is measured.
 *
 * In-plane coordinates: X along the line of nodes, Y in the plane 90 degrees on; toSky turns
 * them into (node, up, towards observer) with the inclination.
 */
import type { Planet } from "./galaxyData";

export const DAYS_PER_SECOND = 1;
const YEAR_D = 365.25;

export type PhaseSource = "transit" | "periastron" | "unknown";

export interface OrbitModel {
  name: string;
  /** Period (days) and where it came from. */
  P: number;
  P_err: number | null;
  P_source: "archive" | "kepler3";
  /** Semi-major axis (AU) and where it came from. */
  a: number;
  a_err: number | null;
  a_source: "archive" | "kepler3";
  e: number;
  /** Argument of periastron (rad). Only meaningful when shape is "measured" and e > 0. */
  omega: number;
  omega_known: boolean;
  /** What the drawn shape rests on (see module docs). */
  shape: "measured" | "circle" | "band";
  /** Inclination to the line of sight (rad); 90° = edge-on. */
  incl: number;
  incl_known: boolean;
  /** Mean anomaly at the epoch, and the epoch (BJD); NaN when the phase is unknown. */
  M0: number;
  T: number;
  T_err: number;
  phase: PhaseSource;
}

/** Julian Date now (UTC). Archive epochs are BJD_TDB; the difference (≤ ~9 min) is far below the uncertainties shown. */
export function jdNow(ms = Date.now()): number {
  return ms / 86400000 + 2440587.5;
}

/** Eccentric anomaly E from mean anomaly M (Newton iteration). */
export function solveKepler(M: number, e: number): number {
  const m = ((M % (2 * Math.PI)) + 3 * Math.PI) % (2 * Math.PI) - Math.PI; // wrap to [-π, π)
  let E = e < 0.8 ? m : Math.PI * Math.sign(m || 1);
  for (let k = 0; k < 30; k++) {
    const d = (E - e * Math.sin(E) - m) / (1 - e * Math.cos(E));
    E -= d;
    if (Math.abs(d) < 1e-12) break;
  }
  return E;
}

export function trueFromEcc(E: number, e: number): number {
  return 2 * Math.atan2(Math.sqrt(1 + e) * Math.sin(E / 2), Math.sqrt(1 - e) * Math.cos(E / 2));
}

function eccFromTrue(f: number, e: number): number {
  return 2 * Math.atan2(Math.sqrt(1 - e) * Math.sin(f / 2), Math.sqrt(1 + e) * Math.cos(f / 2));
}

const num = (v: number | null | undefined): number | null => (v != null && Number.isFinite(v) ? v : null);

/**
 * Orbit of one planet from its archive elements, or null when neither a nor P can be had
 * (nothing is invented: such planets are listed but not drawn).
 */
export function orbitFor(p: Planet, starMass: number | null, index = 0): OrbitModel | null {
  let P = num(p.period_d), a = num(p.a_au);
  const Ms = num(starMass);
  let P_source: OrbitModel["P_source"] = "archive", a_source: OrbitModel["a_source"] = "archive";
  if (P == null && a != null && Ms != null && Ms > 0) { P = YEAR_D * Math.sqrt(a ** 3 / Ms); P_source = "kepler3"; }
  if (a == null && P != null && Ms != null && Ms > 0) { a = Math.cbrt(Ms * (P / YEAR_D) ** 2); a_source = "kepler3"; }
  if (P == null || a == null || P <= 0 || a <= 0) return null;
  const eRaw = num(p.ecc), eErr = num(p.ecc_err);
  const w = num(p.omega_deg);
  const eMeasured = eRaw != null && eRaw >= 0 && eRaw < 1 && (eRaw > 0 || (eErr != null && eErr > 0));
  const shape: OrbitModel["shape"] = !eMeasured ? "circle" : eRaw! > 0 && w == null ? "band" : "measured";
  const e = shape === "circle" ? 0 : eRaw!;
  // For a circle omega has no meaning; pi/2 just puts periastron at the transit point.
  const omega = shape === "measured" && w != null ? (w * Math.PI) / 180 : Math.PI / 2;
  const iDeg = num(p.incl_deg);
  const base = { name: p.name, P, P_err: P_source === "archive" ? num(p.period_err) : null, P_source,
    a, a_err: a_source === "archive" ? num(p.a_err) : null, a_source, e, omega, omega_known: w != null, shape,
    incl: iDeg != null ? (iDeg * Math.PI) / 180 : Math.PI / 2, incl_known: iDeg != null };
  const T0 = num(p.t_transit), Tp = num(p.t_peri);
  if (T0 != null && T0 > 2.0e6) {
    // At mid-transit the planet lies along the line of sight: true anomaly f = π/2 − ω.
    const E = eccFromTrue(Math.PI / 2 - omega, e);
    return { ...base, M0: E - e * Math.sin(E), T: T0, T_err: num(p.t_transit_err) ?? 0, phase: "transit" };
  }
  // A periastron time fixes the phase only if the ellipse's orientation (omega) is known.
  if (Tp != null && Tp > 2.0e6 && shape === "measured") return { ...base, M0: 0, T: Tp, T_err: num(p.t_peri_err) ?? 0, phase: "periastron" };
  void index;
  return { ...base, M0: NaN, T: NaN, T_err: NaN, phase: "unknown" };
}

/** True when the archive supports drawing where the planet is (measured epoch and orientation). */
export function hasPosition(o: OrbitModel): boolean {
  return o.phase !== "unknown" && o.shape !== "band";
}

/** Mean anomaly at Julian date jd (NaN when the phase is unknown). */
export function meanAnomaly(o: OrbitModel, jd: number): number {
  if (o.phase === "unknown") return NaN;
  return o.M0 + (2 * Math.PI * (jd - o.T)) / o.P;
}

/** 1σ uncertainty of the mean anomaly at jd (radians); Infinity when the phase is unknown. */
export function phaseSigma(o: OrbitModel, jd: number): number {
  if (o.phase === "unknown") return Infinity;
  const n = (jd - o.T) / o.P;
  const sP = o.P_err ?? 0;
  if (o.P_err == null && Math.abs(n) > 1) return Infinity; // period itself was derived: no error budget
  return ((2 * Math.PI) / o.P) * Math.hypot(o.T_err, n * sP);
}

/** In-plane position (AU) for mean anomaly M: x along the line of nodes, y towards the observer. */
export function positionAtM(o: OrbitModel, M: number): [number, number] {
  const E = solveKepler(M, o.e);
  const f = trueFromEcc(E, o.e);
  const r = o.a * (1 - o.e * Math.cos(E));
  const u = o.omega + f;
  return [r * Math.cos(u), r * Math.sin(u)];
}

export function positionAt(o: OrbitModel, jd: number): [number, number] {
  return positionAtM(o, meanAnomaly(o, jd));
}

/** In-plane (X, Y) to (along the line of nodes, up on the sky, towards the observer), using i. */
export function toSky(o: OrbitModel, [X, Y]: [number, number]): [number, number, number] {
  return [X, Y * Math.cos(o.incl), Y * Math.sin(o.incl)];
}

/** Circle of radius r (AU) in the orbit's plane, for the "band" shape's inner and outer edges. */
export function circlePoints(r: number, n = 180): [number, number][] {
  return Array.from({ length: n + 1 }, (_, k) => [r * Math.cos((k / n) * 2 * Math.PI), r * Math.sin((k / n) * 2 * Math.PI)] as [number, number]);
}

/**
 * Inner and outer edges of the conservative habitable zone (AU) from Kopparapu et al. (2014,
 * ApJL 787, L29), 1 Earth-mass case: runaway greenhouse to maximum greenhouse. Needs the star's
 * measured temperature and radius (L = R^2 (T/5772)^4, Stefan-Boltzmann); null otherwise.
 * The fit is valid for 2600-7200 K; outside that range it returns null rather than extrapolating.
 */
export function habitableZone(teff: number | null, rad: number | null): [number, number] | null {
  if (teff == null || rad == null || !(teff >= 2600 && teff <= 7200) || !(rad > 0)) return null;
  const L = rad ** 2 * (teff / 5772) ** 4;
  const t = teff - 5780;
  const seff = (s0: number, a: number, b: number, c: number, d: number) => s0 + a * t + b * t ** 2 + c * t ** 3 + d * t ** 4;
  const inner = seff(1.107, 1.332e-4, 1.58e-8, -8.308e-12, -1.931e-15);
  const outer = seff(0.356, 6.171e-5, 1.698e-9, -3.198e-12, -5.575e-16);
  return [Math.sqrt(L / inner), Math.sqrt(L / outer)];
}

/** Points along the full ellipse (AU, same frame), for drawing the path. */
export function ellipsePoints(o: OrbitModel, n = 180): [number, number][] {
  return Array.from({ length: n + 1 }, (_, k) => {
    const f = (k / n) * 2 * Math.PI;
    const r = (o.a * (1 - o.e * o.e)) / (1 + o.e * Math.cos(f));
    return [r * Math.cos(o.omega + f), r * Math.sin(o.omega + f)] as [number, number];
  });
}

/** Short human description of where a planet's position comes from at jd. */
export function phaseNote(o: OrbitModel, jd: number): string {
  if (o.shape === "band") return "eccentricity measured but not its orientation (ω): the band shows its distance range, no position drawn";
  if (o.phase === "unknown") return "no transit or periastron time: orbit drawn, position not drawn";
  const s = phaseSigma(o, jd);
  const deg = (s * 180) / Math.PI;
  const from = o.phase === "transit" ? "transit time" : "periastron time";
  const extra = (o.shape === "circle" ? "; shape not measured, drawn as a circle" : "") + (o.incl_known ? "" : "; tilt not measured, drawn edge-on");
  if (!Number.isFinite(s) || deg >= 180) return `from ${from}, but the uncertainty has grown to the whole orbit${extra}`;
  return `from ${from}, ±${deg < 1 ? deg.toFixed(2) : deg.toFixed(0)}° along the orbit${extra}`;
}
