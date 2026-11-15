/**
 * Backend client. All science processing happens in the backend (Workstream 2);
 * the viewer only requests products and reads the processed FITS to display them.
 *
 * Set VITE_USE_MOCKS=true to run against built-in synthetic data (no backend).
 */
import type {
  AnalysisResponse,
  CatalogResponse,
  Channel,
  CompareResponse,
  CompareSelector,
  ImagePlane,
  LightCurve,
  ProcessResponse,
  SearchResponse,
  Spectrum,
  TargetInfo,
} from "../types";
import { findHdu, firstImage, headerWcs, readFits } from "./fits";

export interface ExploreParams {
  /** Name or coordinate text resolved by the backend; takes precedence over ra/dec. */
  q?: string;
  ra?: number;
  dec?: number;
  fov: number;
  detectors: number[];
  release?: string[];
  date_start?: string;
  date_end?: string;
  wavelength_min?: number;
  wavelength_max?: number;
  max_observations?: number;
}

export interface CompareParams {
  a: CompareSelector;
  b: CompareSelector;
  mode: "difference" | "ratio";
  psf_matching?: boolean;
  match_background?: boolean;
  match_wavelength?: boolean;
  smooth_fwhm_arcsec?: number;
  allow_mixed_detectors?: boolean;
  stack_method?: "mean" | "median" | "weighted";
}

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "true";
const BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

export function apiUrl(path: string): string {
  if (/^(https?:|mock:|blob:|data:)/.test(path)) return path;
  return BASE + path;
}

async function request<T>(path: string, init?: RequestInit & { signal?: AbortSignal }): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), init);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("Cannot reach the SPHEREx Explorer backend. Is it running on port 8000?", 0);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not JSON */
    }
    throw new ApiError(msg, res.status);
  }
  return res.json() as Promise<T>;
}

function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal });
}

function searchBody(p: ExploreParams): Record<string, unknown> {
  const b: Record<string, unknown> = { fov: p.fov, detectors: p.detectors.length ? p.detectors : null };
  if (p.q) b.q = p.q;
  else {
    b.ra = String(p.ra);
    b.dec = String(p.dec);
  }
  for (const k of ["release", "date_start", "date_end", "wavelength_min", "wavelength_max", "max_observations"] as const)
    if (p[k] !== undefined && p[k] !== "") b[k] = p[k];
  return b;
}

async function mocks() {
  return import("../mocks/mockApi");
}

export const api = {
  usingMocks: USE_MOCKS,

  async targets(): Promise<TargetInfo[]> {
    if (USE_MOCKS) return (await mocks()).targets();
    return (await request<{ targets: TargetInfo[] }>("/api/targets")).targets;
  },

  /** Person 1: observations covering the field (with predicted target positions for named targets). */
  async search(p: ExploreParams, signal?: AbortSignal): Promise<SearchResponse> {
    if (USE_MOCKS) return (await mocks()).search(p);
    return post<SearchResponse>("/api/search", searchBody(p), signal);
  },

  /** Person 2: every observation aligned onto one north-up grid; optional stacks. */
  async process(p: ExploreParams, stack?: { by: string; method: string }, signal?: AbortSignal): Promise<ProcessResponse> {
    if (USE_MOCKS) return (await mocks()).process(p, stack?.by);
    const body = { ...searchBody(p), make_gifs: false, ...(stack ? { stack_by: stack.by, stack_method: stack.method } : {}) };
    return post<ProcessResponse>("/api/process", body, signal);
  },

  /** Person 2: difference / ratio of two observation sets (PSF- and background-matched). */
  async compare(p: ExploreParams, c: CompareParams, signal?: AbortSignal): Promise<CompareResponse> {
    if (USE_MOCKS) return (await mocks()).compare(p, c);
    return post<CompareResponse>("/api/compare", { ...searchBody(p), ...c }, signal);
  },

  async channels(): Promise<Channel[]> {
    if (USE_MOCKS) return (await mocks()).channels();
    return (await request<{ channels: Channel[] }>("/api/channels")).channels;
  },

  async catalog(name: "gaia" | "catwise", ra: number, dec: number, radius: number, signal?: AbortSignal): Promise<CatalogResponse> {
    if (USE_MOCKS) return (await mocks()).catalog(name, ra, dec, radius);
    const q = new URLSearchParams({ ra: String(ra), dec: String(dec), radius: radius.toFixed(4), limit: "2000" });
    return request<CatalogResponse>(`/api/catalog/${name}?${q}`, { signal });
  },

  /**
   * Person 4 (optional): candidate / source markers, light curves and spectra.
   * Expected at GET /api/analysis?ra&dec&fov -> { sources, light_curves, spectra }.
   * A 404 (not deployed yet) is treated as "no analysis available".
   */
  async analysis(ra: number, dec: number, fov: number, signal?: AbortSignal, target?: string): Promise<AnalysisResponse> {
    const empty: AnalysisResponse = { sources: [], light_curves: [], spectra: [] };
    if (USE_MOCKS) return { ...empty, ...(await (await mocks()).analysis(ra, dec)) };
    try {
      const q = new URLSearchParams({ ra: String(ra), dec: String(dec), fov: String(fov), full: "false" });
      if (target) q.set("target", target);
      return { ...empty, ...(await request<AnalysisResponse>(`/api/analysis?${q}`, { signal })) };
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 405)) return empty;
      throw e;
    }
  },

  /** Person 4: one source's light curve and spectrum (ids are local to the field given by ra/dec/fov). */
  async sourceProducts(id: string, ra: number, dec: number, fov: number, target?: string, signal?: AbortSignal): Promise<{ light_curve: LightCurve | null; spectra: Spectrum[] }> {
    if (USE_MOCKS) return { light_curve: null, spectra: [] };
    const q = new URLSearchParams({ ra: String(ra), dec: String(dec), fov: String(fov) });
    if (target) q.set("target", target);
    const [lc, sp] = await Promise.all([
      request<{ label: string; light_curve: { points: { mjd: number; flux: number | null; flux_err: number | null; wavelength: number; observation_id: string }[] } }>(`/api/light-curve/${encodeURIComponent(id)}?${q}`, { signal }),
      request<{ label: string; spectrum: { visits: { epoch: number; mjd?: number; points: { wavelength: number; flux: number | null; flux_err: number | null }[] }[] } }>(`/api/spectrum/${encodeURIComponent(id)}?${q}`, { signal }),
    ]);
    const pts = lc.light_curve.points.filter((p) => p.flux !== null).map((p) => ({ mjd: p.mjd, flux: p.flux as number, flux_err: p.flux_err ?? undefined, wavelength: p.wavelength, observation_id: p.observation_id }));
    return {
      light_curve: pts.length ? { source_id: id, label: `${id} ${lc.label}`, units: "mJy", points: pts } : null,
      spectra: sp.spectrum.visits.filter((v) => v.points.length > 1).map((v) => ({
        source_id: id, units: "mJy", label: `${id} visit ${v.epoch + 1}${v.mjd ? " " + mjdDate(v.mjd) : ""}`,
        points: v.points.filter((p) => p.flux !== null).map((p) => ({ wavelength: p.wavelength, flux: p.flux as number, flux_err: p.flux_err ?? undefined })),
      })),
    };
  },
};

function mjdDate(mjd: number): string {
  return new Date((mjd - 40587) * 86400000).toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// FITS planes (cached)
// ---------------------------------------------------------------------------

const planeCache = new Map<string, Promise<ImagePlane>>();
const MAX_CACHE = 600;

/**
 * Load one image plane of a processed FITS file.
 * `extnames` are tried in order (e.g. ["SCI"], ["DIFF", "RATIO"], ["SIG"]); the MASK plane is attached when present.
 */
export function loadPlane(url: string, extnames: string[] = ["SCI"]): Promise<ImagePlane> {
  const key = `${url}#${extnames.join(",")}`;
  let p = planeCache.get(key);
  if (!p) {
    p = (async () => {
      if (url.startsWith("mock:")) return (await mocks()).plane(url, extnames);
      const res = await fetch(apiUrl(url));
      if (!res.ok) throw new ApiError(`Could not load image (${res.status})`, res.status);
      const hdus = readFits(await res.arrayBuffer());
      const h = findHdu(hdus, ...extnames) ?? firstImage(hdus);
      if (!h || !h.data) throw new Error("No image data in FITS file");
      const m = findHdu(hdus, "MASK");
      const mask = m && m.data && m.width === h.width && m.height === h.height ? Uint8Array.from(m.data, (v) => (v ? 1 : 0)) : undefined;
      return { width: h.width, height: h.height, data: h.data, mask, wcs: headerWcs(h.header), units: String(h.header.BUNIT ?? "") };
    })();
    p.catch(() => planeCache.delete(key));
    planeCache.set(key, p);
    if (planeCache.size > MAX_CACHE) planeCache.delete(planeCache.keys().next().value as string);
  }
  return p;
}

/** Load many planes with limited concurrency, reporting progress. */
export async function loadPlanes(
  urls: string[],
  onLoaded: (i: number, plane: ImagePlane | null, err?: Error) => void,
  opts: { concurrency?: number; signal?: AbortSignal; order?: number[]; extnames?: string[] } = {},
): Promise<void> {
  const order = opts.order ?? urls.map((_, i) => i);
  let next = 0;
  const worker = async () => {
    while (next < order.length && !opts.signal?.aborted) {
      const i = order[next++];
      try {
        onLoaded(i, await loadPlane(urls[i], opts.extnames));
      } catch (e) {
        onLoaded(i, null, e as Error);
      }
    }
  };
  await Promise.all(Array.from({ length: opts.concurrency ?? 6 }, worker));
}
