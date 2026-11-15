/**
 * Synthetic stand-in for the backend (VITE_USE_MOCKS=true), for UI work and demos
 * without network access. It produces the same JSON shapes as the real API.
 *
 * The sky is fake: a fast-moving star (Barnard-like proper motion), ~25 static
 * stars, Gaussian PSF, noise, irregular visits in three epochs ~6 months apart,
 * and the occasional masked column. Nothing here is real SPHEREx data.
 */
import type {
  CatalogResponse,
  Channel,
  CompareResponse,
  FitsWcs,
  ImagePlane,
  LightCurve,
  ProcessResponse,
  ProcessedObservation,
  SearchResponse,
  SourceMarker,
  Spectrum,
  StackProduct,
  TargetInfo,
} from "../types";
import type { CompareParams, ExploreParams } from "../lib/api";
import { propagate } from "../lib/catalog";
import { makeTan, world2pix } from "../lib/wcs";
import { mjdToDate } from "../lib/time";
import { MANDATORY_TARGETS } from "../lib/targets";

const PIX = 6.15;
const DETECTOR_RANGES: Record<number, [number, number]> = {
  1: [0.7455, 1.1167], 2: [1.1086, 1.6409], 3: [1.6402, 2.4202],
  4: [2.4189, 3.8239], 5: [3.8189, 4.4185], 6: [4.4115, 5.0096],
};
const TARGET_POS: Record<string, { ra: number; dec: number; pm: [number, number] }> = {
  "barnard's star": { ra: 269.44622, dec: 4.76879, pm: [-801.55, 10362.39] },
  "eps ind b": { ra: 331.0874, dec: -56.7966, pm: [3982, -2469] },
  "wise j085510.83-071442.5": { ra: 133.7595, dec: -7.2423, pm: [-8118, 680] },
  "rw aur": { ra: 76.9564, dec: 30.4012, pm: [3.6, -26.6] },
  "r dra": { ra: 246.1708, dec: 66.7531, pm: [-4.5, 12.7] },
};
const REF_MJD = 61100;

function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gauss(r: () => number): number {
  return Math.sqrt(-2 * Math.log(r() + 1e-12)) * Math.cos(2 * Math.PI * r());
}

interface MockFrame {
  meta: ProcessedObservation;
  plane: ImagePlane;
}
interface MockSeries {
  key: string;
  ra: number;
  dec: number;
  name: string | null;
  pm: [number, number];
  wcs: FitsWcs;
  n: number;
  frames: MockFrame[];
  stars: { ra: number; dec: number; flux: number }[];
}

const seriesCache = new Map<string, MockSeries>();
const extraPlanes = new Map<string, ImagePlane>();

function centerFor(p: ExploreParams) {
  const t = p.q ? TARGET_POS[p.q.trim().toLowerCase()] : undefined;
  if (t) return { ...t, name: p.q ?? null };
  return { ra: p.ra ?? 269.44622, dec: p.dec ?? 4.76879, pm: [-801.55, 10362.39] as [number, number], name: null };
}

function buildSeries(p: ExploreParams): MockSeries {
  const c = centerFor(p);
  const dets = p.detectors.length ? p.detectors : [6];
  const key = `${c.ra.toFixed(4)}_${c.dec.toFixed(4)}_${p.fov}_${dets.join("")}`;
  const hit = seriesCache.get(key);
  if (hit) return hit;
  let n = Math.round((p.fov * 3600) / PIX);
  n = Math.min(101, Math.max(11, n % 2 ? n : n + 1));
  const wcs: FitsWcs = {
    CTYPE1: "RA---TAN", CTYPE2: "DEC--TAN", CRPIX1: (n + 1) / 2, CRPIX2: (n + 1) / 2,
    CRVAL1: c.ra, CRVAL2: c.dec, CDELT1: -PIX / 3600, CDELT2: PIX / 3600, CUNIT1: "deg", CUNIT2: "deg", RADESYS: "ICRS",
  };
  const tan = makeTan(wcs);
  const r = rng(Math.round(c.ra * 1000 + c.dec * 7));
  const half = (n * PIX) / 3600 / 2;
  const stars = Array.from({ length: 25 }, () => ({
    ra: c.ra + ((r() * 2 - 1) * half) / Math.cos((c.dec * Math.PI) / 180),
    dec: c.dec + (r() * 2 - 1) * half,
    flux: 0.3 + 8 * r() ** 4,
  }));
  const epochs = [60902.86, 61085.2, 61268.6];
  const frames: MockFrame[] = [];
  let k = 0;
  for (const e0 of epochs) {
    const nvis = 10 + Math.floor(r() * 6);
    let t = e0;
    for (let v = 0; v < nvis; v++) {
      t += v === 0 ? 0 : r() < 0.6 ? 0.0015 + r() * 0.002 : 0.3 + r() * 2.5;
      for (const det of dets) {
        const [w0, w1] = DETECTOR_RANGES[det];
        const wl = w0 + (w1 - w0) * r();
        frames.push(makeFrame(k++, t, det, wl, n, wcs, tan, stars, c, r, key));
      }
    }
  }
  frames.sort((a, b) => a.meta.mjd - b.meta.mjd);
  const s: MockSeries = { key, ra: c.ra, dec: c.dec, name: c.name, pm: c.pm, wcs, n, frames, stars };
  seriesCache.set(key, s);
  return s;
}

function makeFrame(k: number, mjd: number, det: number, wl: number, n: number, wcs: FitsWcs, tan: ReturnType<typeof makeTan>,
  stars: MockSeries["stars"], c: { ra: number; dec: number; pm: [number, number] }, r: () => number, key: string): MockFrame {
  const fwhm = 7.5 + (det - 1) * 0.3;
  const sig = fwhm / 2.355 / PIX;
  const data = new Float32Array(n * n);
  const noise = 0.04 + 0.02 * det / 6;
  for (let i = 0; i < data.length; i++) data[i] = noise * gauss(r);
  const [tra, tdec] = propagate({ ra: c.ra, dec: c.dec, ref_mjd: REF_MJD, pm_ra_cosdec_mas_yr: c.pm[0], pm_dec_mas_yr: c.pm[1] }, mjd);
  const all = [...stars, { ra: tra, dec: tdec, flux: 12 * (wl / 3) ** -0.8 }];
  for (const s of all) {
    const pxy = world2pix(tan, s.ra, s.dec);
    if (!pxy) continue;
    const [px, py] = pxy;
    const x0 = Math.max(0, Math.floor(px - 4 * sig)), x1 = Math.min(n - 1, Math.ceil(px + 4 * sig));
    const y0 = Math.max(0, Math.floor(py - 4 * sig)), y1 = Math.min(n - 1, Math.ceil(py + 4 * sig));
    for (let y = y0; y <= y1; y++)
      for (let x = x0; x <= x1; x++) data[y * n + x] += s.flux * Math.exp(-((x - px) ** 2 + (y - py) ** 2) / (2 * sig * sig));
  }
  const mask = new Uint8Array(n * n);
  const flags: string[] = [];
  if (r() < 0.15) {
    const col = Math.floor(r() * n);
    for (let y = 0; y < n; y++) mask[y * n + col] = 1;
    flags.push("NONFUNC");
  }
  if (r() < 0.2) flags.push("TRANSIENT");
  const date = mjdToDate(mjd).toISOString().replace(/\.\d+Z$/, ".000Z");
  const id = `MOCK${String(k).padStart(4, "0")}_D${det}`;
  const chIdx = (det - 1) * 17 + Math.min(16, Math.floor(17 * Math.log(wl / DETECTOR_RANGES[det][0]) / Math.log(DETECTOR_RANGES[det][1] / DETECTOR_RANGES[det][0])));
  const ch = channelList()[chIdx];
  const valid = 1 - mask.reduce((a, b) => a + b, 0) / mask.length;
  const meta: ProcessedObservation = {
    observation_id: id, observation_time: date, wavelength: +wl.toFixed(5), mjd,
    image_url: "", fits_url: `mock://frame/${key}/${id}`, wcs, quality_flags: flags,
    processing_method: "MOCK synthetic frame (not real data)", detector: det, bandwidth: +(wl / 130).toFixed(5),
    channel: { index: ch.index, label: ch.label, wavelength_min: ch.wavelength_min, wavelength_max: ch.wavelength_max, detector: det },
    data_release: "MOCK", valid_fraction: +valid.toFixed(4), background_level: 0, zodi_median: 0.3, psf_fwhm_arcsec: fwhm,
    masked_flags: flags.includes("NONFUNC") ? ["NONFUNC"] : [], display_limits: [-0.1, 6], units: "MJy/sr",
    history: ["synthetic mock frame"],
  };
  return { meta, plane: { width: n, height: n, data, mask, wcs, units: "MJy/sr" } };
}

let channelsMemo: Channel[] | null = null;
export function channelList(): Channel[] {
  if (channelsMemo) return channelsMemo;
  const out: Channel[] = [];
  for (let d = 1; d <= 6; d++) {
    const [a, b] = DETECTOR_RANGES[d];
    for (let j = 0; j < 17; j++) {
      const lo = a * (b / a) ** (j / 17), hi = a * (b / a) ** ((j + 1) / 17);
      const ctr = Math.sqrt(lo * hi);
      out.push({ index: (d - 1) * 17 + j, detector: d, local_index: j, wavelength_min: lo, wavelength_max: hi, wavelength_center: ctr, label: `D${d}-${String(j + 1).padStart(2, "0")} (${ctr.toFixed(3)} um)` });
    }
  }
  return (channelsMemo = out);
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

function queryOf(s: MockSeries, p: ExploreParams) {
  return {
    query: p.q ?? `${p.ra} ${p.dec}`, ra: s.ra, dec: s.dec, kind: p.q ? "name" : "coordinates", name: s.name, epoch: p.q ? "2026-03-01T00:00:00.000" : null,
    target: p.q ? { query: p.q, name: s.name, ra: s.ra, dec: s.dec, ref_epoch: "MJD 61100", pm_ra_cosdec_mas_yr: s.pm[0], pm_dec_mas_yr: s.pm[1], parallax_mas: null, object_type: "MOCK" } : null,
  };
}

export async function targets(): Promise<TargetInfo[]> {
  return MANDATORY_TARGETS;
}

export async function channels(): Promise<Channel[]> {
  return channelList();
}

export async function search(p: ExploreParams): Promise<SearchResponse> {
  await delay(150);
  const s = buildSeries(p);
  const observations = s.frames.map((f) => {
    const [tra, tdec] = propagate({ ra: s.ra, dec: s.dec, ref_mjd: REF_MJD, pm_ra_cosdec_mas_yr: s.pm[0], pm_dec_mas_yr: s.pm[1] }, f.meta.mjd);
    return {
      observation_id: f.meta.observation_id, observation_time: f.meta.observation_time, ra: s.ra, dec: s.dec,
      wavelength_min: f.meta.wavelength - 0.02, wavelength_max: f.meta.wavelength + 0.02, band: `D${f.meta.detector}`,
      detector: f.meta.detector, data_release: "MOCK", quality_flags: f.meta.quality_flags, fits_url: "", mjd_start: f.meta.mjd,
      ...(p.q ? { target_predicted_ra: tra, target_predicted_dec: tdec } : {}),
    };
  });
  return { query: queryOf(s, p), count: observations.length, observations, epochs: [] };
}

export async function process(p: ExploreParams, stackBy?: string): Promise<ProcessResponse> {
  await delay(400);
  const s = buildSeries(p);
  const out: ProcessResponse = {
    grid: { center_ra: s.ra, center_dec: s.dec, fov_deg: p.fov, shape: [s.n, s.n], pixel_scale_arcsec: PIX, orientation: "north up, east left", wcs: s.wcs },
    count: s.frames.length, observations: s.frames.map((f) => f.meta), animations: {}, errors: [], skipped: [], query: queryOf(s, p),
  };
  if (stackBy) out.stacks = makeStacks(s);
  return out;
}

function meanPlane(frames: MockFrame[], wcs: FitsWcs, n: number): ImagePlane {
  const data = new Float32Array(n * n), cnt = new Uint16Array(n * n);
  for (const f of frames)
    for (let i = 0; i < data.length; i++)
      if (!f.plane.mask?.[i]) {
        data[i] += f.plane.data[i];
        cnt[i]++;
      }
  for (let i = 0; i < data.length; i++) data[i] = cnt[i] ? data[i] / cnt[i] : NaN;
  return { width: n, height: n, data, wcs, units: "MJy/sr" };
}

function makeStacks(s: MockSeries): StackProduct[] {
  const groups: MockFrame[][] = [];
  s.frames.forEach((f, i) => {
    if (i === 0 || f.meta.mjd - s.frames[i - 1].meta.mjd > 20) groups.push([]);
    groups[groups.length - 1].push(f);
  });
  return groups.map((g, i) => {
    const url = `mock://stack/${s.key}/epoch${i}`;
    extraPlanes.set(url, meanPlane(g, s.wcs, s.n));
    const wl = g.map((f) => f.meta.wavelength);
    return {
      key: `epoch${i}_D${g[0].meta.detector}`, group_by: "epoch", method: "mean", n_frames: g.length, observation_ids: g.map((f) => f.meta.observation_id),
      detectors: [...new Set(g.map((f) => f.meta.detector))], time_start: g[0].meta.observation_time, time_end: g[g.length - 1].meta.observation_time,
      mjd_mean: g.reduce((a, f) => a + f.meta.mjd, 0) / g.length, wavelength_mean: wl.reduce((a, b) => a + b, 0) / wl.length,
      wavelength_min: Math.min(...wl), wavelength_max: Math.max(...wl), warnings: ["mock stack: members span several wavelengths"],
      image_url: "", fits_url: url, valid_fraction: 1, display_limits: null, wcs: s.wcs,
    };
  });
}

export async function compare(p: ExploreParams, c: CompareParams): Promise<CompareResponse> {
  await delay(500);
  const s = buildSeries(p);
  const pick = (ids?: string[]) => s.frames.filter((f) => ids?.includes(f.meta.observation_id));
  const A = pick(c.a.observation_ids), B = pick(c.b.observation_ids);
  if (!A.length || !B.length) throw new Error("Selection is empty");
  const a = meanPlane(A, s.wcs, s.n), b = meanPlane(B, s.wcs, s.n);
  const out = new Float32Array(a.data.length), sig = new Float32Array(a.data.length);
  for (let i = 0; i < out.length; i++) {
    out[i] = c.mode === "difference" ? a.data[i] - b.data[i] : b.data[i] > 0.25 ? a.data[i] / b.data[i] : NaN;
    sig[i] = (a.data[i] - b.data[i]) / 0.07;
  }
  const name = `mock_${c.mode}_${A[0].meta.observation_id}_${B[0].meta.observation_id}`;
  extraPlanes.set(`mock://compare/${name}#DIFF,RATIO`, { ...a, data: out });
  extraPlanes.set(`mock://compare/${name}#SIG`, { ...a, data: sig });
  return {
    mode: c.mode, name, image_url: "", significance_url: "", fits_url: `mock://compare/${name}`,
    a: { observation_ids: A.map((f) => f.meta.observation_id) }, b: { observation_ids: B.map((f) => f.meta.observation_id) },
    stats: { delta_t_days: A[0].meta.mjd - B[0].meta.mjd, significance_robust_std: 1.0, valid_fraction: 1 },
    caveats: ["Mock comparison on synthetic data (no PSF matching)."], history: [], wcs: s.wcs,
    display: c.mode === "difference" ? "diverging blue-white-red: red = brighter in A, blue = brighter in B" : "diverging around ratio 1: red = A/B > 1",
  };
}

export async function plane(url: string, extnames: string[]): Promise<ImagePlane> {
  const direct = extraPlanes.get(`${url}#${extnames.join(",")}`) ?? extraPlanes.get(url);
  if (direct) return direct;
  const [, , key, id] = url.replace("mock://", "mock:/").split("/");
  const f = seriesCache.get(key)?.frames.find((x) => x.meta.observation_id === id);
  if (!f) throw new Error(`mock plane not found: ${url}`);
  await delay(5);
  return f.plane;
}

export async function catalog(name: "gaia" | "catwise", ra: number, dec: number, radius: number): Promise<CatalogResponse> {
  await delay(200);
  const s = [...seriesCache.values()].find((x) => Math.abs(x.ra - ra) < 0.01 && Math.abs(x.dec - dec) < 0.01);
  const ref = name === "gaia" ? 57388 : 57200;
  const sources = (s?.stars ?? []).map((st, i) => ({
    id: `${name === "gaia" ? "Gaia DR3 MOCK" : "CWISE MOCK"}${i}`, ra: st.ra, dec: st.dec, ref_mjd: ref,
    pm_ra_cosdec_mas_yr: 0, pm_dec_mas_yr: 0, parallax_mas: null, mag: +(14 - 2.5 * Math.log10(st.flux)).toFixed(2), mag_band: name === "gaia" ? "G" : "W2",
  }));
  if (s) {
    const [r0, d0] = propagate({ ra: s.ra, dec: s.dec, ref_mjd: REF_MJD, pm_ra_cosdec_mas_yr: s.pm[0], pm_dec_mas_yr: s.pm[1] }, ref);
    sources.unshift({ id: `${name === "gaia" ? "Gaia DR3" : "CWISE"} MOCK-target`, ra: r0, dec: d0, ref_mjd: ref, pm_ra_cosdec_mas_yr: s.pm[0], pm_dec_mas_yr: s.pm[1], parallax_mas: null, mag: 9, mag_band: name === "gaia" ? "G" : "W2" });
  }
  return { catalog: name, center_ra: ra, center_dec: dec, radius_deg: radius, count: sources.length, sources };
}

export async function analysis(ra: number, dec: number): Promise<{ sources: SourceMarker[]; light_curves: LightCurve[]; spectra: Spectrum[] }> {
  const s = [...seriesCache.values()].find((x) => Math.abs(x.ra - ra) < 0.01 && Math.abs(x.dec - dec) < 0.01);
  if (!s) return { sources: [], light_curves: [], spectra: [] };
  const src: SourceMarker = { id: "P4-MOCK-1", label: "mover candidate", ra: s.ra, dec: s.dec, mjd: REF_MJD, pm_ra_cosdec_mas_yr: s.pm[0], pm_dec_mas_yr: s.pm[1], kind: "candidate", score: 0.97, notes: "Mock Person 4 detection" };
  const pts = s.frames.map((f) => ({ mjd: f.meta.mjd, flux: 12 * (f.meta.wavelength / 3) ** -0.8 * (1 + 0.03 * Math.sin(f.meta.mjd)), flux_err: 0.2, wavelength: f.meta.wavelength, observation_id: f.meta.observation_id }));
  return {
    sources: [src],
    light_curves: [{ source_id: src.id, label: "mock photometry", points: pts, units: "mJy" }],
    spectra: [{ source_id: src.id, label: "mock spectrum", points: pts.map((p) => ({ wavelength: p.wavelength, flux: p.flux, flux_err: p.flux_err })).sort((a, b) => a.wavelength - b.wavelength), units: "mJy" }],
  };
}
