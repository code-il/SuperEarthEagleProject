/**
 * Shareable URLs: /explore?ra=269.452&dec=4.693&fov=0.1
 *
 * Query parameters
 *   q        target name or coordinate text (resolved by the backend; overrides ra/dec)
 *   ra, dec  decimal degrees, or sexagesimal (RA in hours): ra=17h57m48.5s&dec=+04d41m36s
 *   fov      field of view in degrees (0.005 – 2; default 0.05)
 *   det      detectors, e.g. det=6 or det=1,2 (default 6)
 *   release  DR1, QR3, ... (comma-separated)
 *   from,to  date range (ISO date or MJD)
 *   wmin,wmax wavelength range in micrometres
 *   mode     single | blink | side-by-side | difference | ratio | stacked
 *   obs      observation_id to open at
 *   stretch, cmap   display defaults
 */
import type { ExploreParams } from "./api";
import { CoordinateInputError, parseDec, parseRa, validateFov } from "./coords";
import type { Colormap, Stretch, ViewMode } from "../types";

export const DEFAULT_FOV = 0.05;
export const DEFAULT_DETECTORS = [6];
const MODES: ViewMode[] = ["single", "blink", "side-by-side", "difference", "ratio", "stacked"];
const STRETCHES: Stretch[] = ["linear", "sqrt", "log", "asinh"];
const CMAPS: Colormap[] = ["gray", "inferno", "magma", "viridis"];

export interface UrlState {
  params: ExploreParams | null;
  error: string | null;
  mode?: ViewMode;
  obs?: string;
  stretch?: Stretch;
  cmap?: Colormap;
}

function num(v: string | null): number | undefined {
  if (v === null || v.trim() === "") return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

export function parseUrl(search: URLSearchParams): UrlState {
  const out: UrlState = { params: null, error: null };
  const mode = search.get("mode") as ViewMode | null;
  if (mode && MODES.includes(mode)) out.mode = mode;
  out.obs = search.get("obs") ?? undefined;
  const st = search.get("stretch") as Stretch | null;
  if (st && STRETCHES.includes(st)) out.stretch = st;
  const cm = search.get("cmap") as Colormap | null;
  if (cm && CMAPS.includes(cm)) out.cmap = cm;

  const q = search.get("q")?.trim();
  const ra = search.get("ra"), dec = search.get("dec");
  if (!q && (ra === null || dec === null)) return out;
  try {
    const fov = validateFov(num(search.get("fov")) ?? DEFAULT_FOV);
    const detRaw = search.get("det");
    const detectors = detRaw === "all" ? [] : detRaw
      ? detRaw.split(",").map((d) => Number(d.replace(/^D/i, ""))).filter((d) => Number.isInteger(d) && d >= 1 && d <= 6)
      : DEFAULT_DETECTORS;
    const p: ExploreParams = { fov, detectors };
    if (q) p.q = q;
    else {
      p.ra = parseRa(ra!);
      p.dec = parseDec(dec!);
    }
    const rel = search.get("release");
    if (rel) p.release = rel.split(",").map((s) => s.trim()).filter(Boolean);
    const from = search.get("from"), to = search.get("to");
    if (from) p.date_start = from;
    if (to) p.date_end = to;
    const wmin = num(search.get("wmin")), wmax = num(search.get("wmax"));
    if (wmin !== undefined) p.wavelength_min = wmin;
    if (wmax !== undefined) p.wavelength_max = wmax;
    out.params = p;
  } catch (e) {
    out.error = e instanceof CoordinateInputError ? e.message : String(e);
  }
  return out;
}

/** Every query key the Explore viewer reads (the Atlas strips these when the Explore pop-up closes). */
export const EXPLORE_KEYS = ["q", "ra", "dec", "fov", "det", "release", "from", "to", "wmin", "wmax", "mode", "obs", "stretch", "cmap"];

export function buildUrl(p: ExploreParams, extra: { mode?: ViewMode; obs?: string } = {}): string {
  return `/explore?${exploreSearch(p, extra).toString()}`;
}

/** The Explore query for `p`, as search params (used inside the Atlas pop-up as well as for /explore links). */
export function exploreSearch(p: ExploreParams, extra: { mode?: ViewMode; obs?: string } = {}): URLSearchParams {
  const s = new URLSearchParams();
  if (p.q) s.set("q", p.q);
  else {
    s.set("ra", String(+(p.ra ?? 0).toFixed(6)));
    s.set("dec", String(+(p.dec ?? 0).toFixed(6)));
  }
  s.set("fov", String(p.fov));
  const det = p.detectors.length ? p.detectors.join(",") : "all";
  if (det !== DEFAULT_DETECTORS.join(",")) s.set("det", det);
  if (p.release?.length) s.set("release", p.release.join(","));
  if (p.date_start) s.set("from", p.date_start);
  if (p.date_end) s.set("to", p.date_end);
  if (p.wavelength_min !== undefined) s.set("wmin", String(p.wavelength_min));
  if (p.wavelength_max !== undefined) s.set("wmax", String(p.wavelength_max));
  if (extra.mode && extra.mode !== "single") s.set("mode", extra.mode);
  if (extra.obs) s.set("obs", extra.obs);
  return s;
}
