/**
 * Planet tracks from the backend N-body engine (/api/orbits/system/{i}/positions).
 *
 * The engine integrates the full N-body problem (planets pull on each other), corrected so it
 * reproduces the NASA Exoplanet Archive transit ephemerides. It sends positions and velocities
 * relative to the star on a regular grid; between samples we use cubic Hermite interpolation,
 * which matches position and velocity at both ends (error 2.5e-5 of the orbit radius at 20 samples/orbit, tens of km).
 * Frame: (line of nodes, up on the sky, towards the observer), the same as orbits.ts toSky.
 */
export interface NbodyChunk {
  host: string;
  status: "ok" | "refused" | "not_converged";
  problems?: string[];
  notes: string[];
  t0: number;
  step: number;
  n: number;
  planets: { name: string; pos: number[]; vel: number[] }[];
  iterations?: number;
  worst_sigma?: number;
}

export interface NbodyTrack {
  t0: number;
  step: number;
  n: number;
  byName: Map<string, { pos: Float64Array; vel: Float64Array }>;
}

export function trackFromChunk(c: NbodyChunk): NbodyTrack {
  return {
    t0: c.t0, step: c.step, n: c.n,
    byName: new Map(c.planets.map((p) => [p.name, { pos: Float64Array.from(p.pos), vel: Float64Array.from(p.vel) }])),
  };
}

export function trackEnd(t: NbodyTrack): number {
  return t.t0 + (t.n - 1) * t.step;
}

export function covers(t: NbodyTrack | null, jd: number): boolean {
  return !!t && jd >= t.t0 && jd <= trackEnd(t);
}

/** Position (AU) of a planet at jd, or null outside the track. */
export function trackPosition(t: NbodyTrack, name: string, jd: number): [number, number, number] | null {
  const p = t.byName.get(name);
  if (!p || !covers(t, jd)) return null;
  const x = (jd - t.t0) / t.step;
  const k = Math.min(Math.floor(x), t.n - 2);
  const s = x - k, h = t.step;
  const h00 = 2 * s ** 3 - 3 * s ** 2 + 1, h10 = s ** 3 - 2 * s ** 2 + s, h01 = -2 * s ** 3 + 3 * s ** 2, h11 = s ** 3 - s ** 2;
  const out: [number, number, number] = [0, 0, 0];
  for (let d = 0; d < 3; d++) {
    const i0 = 3 * k + d, i1 = 3 * (k + 1) + d;
    out[d] = h00 * p.pos[i0] + h10 * h * p.vel[i0] + h01 * p.pos[i1] + h11 * h * p.vel[i1];
  }
  return out;
}
