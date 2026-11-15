/** Choosing frames for comparisons. Pure functions (unit-tested). */

export interface FrameLike {
  seriesIndex: number;
  observation_id: string;
  mjd: number;
  wavelength: number;
  detector: number;
  epoch: number;
  channel: { index: number } | null;
}

/**
 * Default reference "B" for frame A: same detector, a different visit, closest in
 * wavelength (ties: furthest in time, to maximise motion). Falls back to the
 * neighbouring frame when there is only one visit.
 */
export function autoReference<F extends FrameLike>(visible: F[], a: F | undefined): F | undefined {
  if (!a || visible.length < 2) return undefined;
  const same = visible.filter((f) => f.detector === a.detector && f.seriesIndex !== a.seriesIndex);
  const pool = same.length ? same : visible.filter((f) => f.seriesIndex !== a.seriesIndex);
  const other = pool.filter((f) => f.epoch !== a.epoch);
  const cands = other.length ? other : pool;
  let best: F | undefined;
  let bestKey = Infinity;
  for (const f of cands) {
    const dwl = Math.abs(f.wavelength - a.wavelength);
    // wavelength first (0.001 um resolution), then prefer larger time separation when using other visits
    const k = Math.round(dwl * 1000) * 1e6 + (other.length ? -Math.abs(f.mjd - a.mjd) : Math.abs(f.mjd - a.mjd));
    if (k < bestKey) {
      bestKey = k;
      best = f;
    }
  }
  return best;
}

/** Frame in channel `ch` closest in time to `a` (for comparing wavelengths at ~one time). */
export function nearestInChannel<F extends FrameLike>(frames: F[], a: F | undefined, ch: number): F | undefined {
  if (!a) return undefined;
  let best: F | undefined;
  let bd = Infinity;
  for (const f of frames) {
    if (f.channel?.index !== ch) continue;
    const d = Math.abs(f.mjd - a.mjd);
    if (d < bd) {
      bd = d;
      best = f;
    }
  }
  return best;
}

/** IDs of the frames in f's visit (same detector) among `visible`. */
export function visitIds<F extends FrameLike>(visible: F[], f: F | undefined): string[] {
  if (!f) return [];
  return visible.filter((x) => x.epoch === f.epoch && x.detector === f.detector).map((x) => x.observation_id);
}

/** Keep the user on the same moment when the visible set changes. */
export function nearestVisible<F extends FrameLike>(visible: F[], mjd: number): F | undefined {
  let best: F | undefined;
  let bd = Infinity;
  for (const f of visible) {
    const d = Math.abs(f.mjd - mjd);
    if (d < bd) {
      bd = d;
      best = f;
    }
  }
  return best;
}
