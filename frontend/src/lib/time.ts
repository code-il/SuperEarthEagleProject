/** Time helpers. SPHEREx frames are irregular in time: never assume equal spacing. */

export const MJD_UNIX_EPOCH = 40587;

export function mjdToDate(mjd: number): Date {
  return new Date((mjd - MJD_UNIX_EPOCH) * 86400_000);
}

export function dateToMjd(d: Date): number {
  return d.getTime() / 86400_000 + MJD_UNIX_EPOCH;
}

export function isoToMjd(iso: string): number {
  return dateToMjd(new Date(iso));
}

export function formatDate(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toISOString().slice(0, 10);
}

export function formatDateTime(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

/** Human time difference: "2 min", "5.3 h", "12.4 d", "1.02 yr". */
export function formatDuration(days: number): string {
  const a = Math.abs(days);
  const sign = days < 0 ? "−" : "";
  if (a < 1 / 24) return `${sign}${(a * 1440).toFixed(a * 1440 < 10 ? 1 : 0)} min`;
  if (a < 2) return `${sign}${(a * 24).toFixed(1)} h`;
  if (a < 365.25 * 1.5) return `${sign}${a.toFixed(1)} d`;
  return `${sign}${(a / 365.25).toFixed(2)} yr`;
}

/** Index of the frame closest in time to `mjd` (frames sorted by mjd). */
export function nearestIndex(mjds: number[], mjd: number): number {
  if (!mjds.length) return -1;
  let lo = 0, hi = mjds.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (mjds[mid] < mjd) lo = mid;
    else hi = mid;
  }
  return Math.abs(mjds[lo] - mjd) <= Math.abs(mjds[hi] - mjd) ? lo : hi;
}

/** Group sorted times into visits separated by gaps longer than `gapDays`. Returns start indices. */
export function groupByGap(mjds: number[], gapDays = 20): { start: number; end: number }[] {
  const out: { start: number; end: number }[] = [];
  mjds.forEach((m, i) => {
    if (i === 0 || m - mjds[i - 1] > gapDays) out.push({ start: i, end: i });
    else out[out.length - 1].end = i;
  });
  return out;
}

/**
 * How long to show frame i during playback.
 * "uniform": every frame gets 1/fps seconds (timeline still shows true spacing).
 * "proportional": duration follows the real gap to the next frame (log-compressed,
 * so a 6-month gap reads as a visible pause without stalling playback).
 */
export function frameDurationMs(mjds: number[], i: number, fps: number, timing: "uniform" | "proportional"): number {
  const base = 1000 / fps;
  if (timing === "uniform" || i >= mjds.length - 1) return base;
  const gap = Math.max(0, mjds[i + 1] - mjds[i]);
  // 0 d -> 0.5x, 1 d -> ~1.5x, 180 d -> ~4x, capped at 6x.
  return base * Math.min(6, 0.5 + Math.log10(1 + gap * 10) * 1.2);
}
