/**
 * Data for the 3D galaxy map (built by scripts/build_galaxy_map.py into public/galaxy/).
 *
 * stars.bin = float32 xyz[n*3] (pc, heliocentric Galactic: +x Galactic centre, +z north pole)
 *           + uint8 attrs[n*4] (class + 16*source, flags, absmag byte, colour byte)
 *           + uint8 confidence[n]
 */

export interface ClassDef { id: number; key: string; label: string; color: string; desc: string }

export interface GalaxyMeta {
  count: number;
  built: string;
  classes: ClassDef[];
  flags: Record<string, number>;
  counts: Record<string, number>;
  class_sources: Record<string, number>;
  model: { method: string; labels: string; n_train: number; n_test: number; accuracy: number;
    accuracy_within_one_type_main_sequence: number | null; per_class: Record<string, { n_test: number; recall: number }> };
  provenance: Record<string, Record<string, unknown>>;
}

/** Named object: i = index into stars, n = name, c = class, f = flags, d = distance pc, t = SIMBAD type, s = spectral type. */
export interface NamedObject { i: number; n: string; c: number; f: number; d: number; t: string; s: string; a?: string[] }

export interface Planet { name: string; a_au: number | null; period_d: number | null; radius_re: number | null; mass_me: number | null;
  teq_k: number | null; ecc: number | null; method: string; year: string }
export interface PlanetSystem { i: number; host: string; dist_pc: number; st_spectype: string; st_teff: number | null;
  st_rad: number | null; st_mass: number | null; planets: Planet[] }

export interface StarBuffers { n: number; xyz: Float32Array; attrs: Uint8Array; conf: Uint8Array }

export const FLAG = { planet_host: 1, variable: 2, multiple: 4, spherex: 8, named: 16, high_velocity: 32, simbad_only: 64, low_confidence: 128 } as const;
export const SOURCES = ["none", "model", "catalogue", "spherex"] as const;
export type ClassSource = (typeof SOURCES)[number];
export const SOURCE_LABEL: Record<ClassSource, string> = {
  spherex: "Measured: SPHEREx spectrum",
  catalogue: "Catalogued: SIMBAD type",
  model: "Predicted: Gaia k-NN model",
  none: "Unclassified",
};

export interface GalaxyData { meta: GalaxyMeta; stars: StarBuffers; objects: NamedObject[]; systems: PlanetSystem[] }

export async function loadGalaxy(onProgress?: (frac: number) => void): Promise<GalaxyData> {
  const [meta, objects, systems] = await Promise.all([
    fetch("/galaxy/meta.json").then(ok).then((r) => r.json() as Promise<GalaxyMeta>),
    fetch("/galaxy/objects.json").then(ok).then((r) => r.json() as Promise<NamedObject[]>),
    fetch("/galaxy/systems.json").then(ok).then((r) => r.json() as Promise<PlanetSystem[]>),
  ]);
  const buf = await fetchWithProgress("/galaxy/stars.bin", onProgress);
  const n = meta.count;
  const xyz = new Float32Array(buf, 0, n * 3);
  const attrs = new Uint8Array(buf, n * 12, n * 4);
  const conf = new Uint8Array(buf, n * 16, n);
  return { meta, stars: { n, xyz, attrs, conf }, objects, systems };
}

function ok(r: Response): Response {
  if (!r.ok) throw new Error(`${r.url}: HTTP ${r.status}. Build the map data with: python -m scripts.build_galaxy_map`);
  return r;
}

async function fetchWithProgress(url: string, onProgress?: (f: number) => void): Promise<ArrayBuffer> {
  const r = ok(await fetch(url));
  const total = Number(r.headers.get("content-length")) || 0;
  if (!r.body || !total || !onProgress) return r.arrayBuffer();
  const reader = r.body.getReader();
  const out = new Uint8Array(total);
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (got + value.length > out.length) return r.arrayBuffer(); // compressed transfer: length mismatch
    out.set(value, got);
    got += value.length;
    onProgress(got / total);
  }
  return out.buffer;
}

export const classOf = (attrs: Uint8Array, i: number) => attrs[i * 4] & 15;
export const sourceOf = (attrs: Uint8Array, i: number): ClassSource => SOURCES[attrs[i * 4] >> 4] ?? "none";
export const absMagOf = (attrs: Uint8Array, i: number) => attrs[i * 4 + 2] / 8 - 8;
export const bpRpOf = (attrs: Uint8Array, i: number) => (attrs[i * 4 + 3] === 0 ? null : attrs[i * 4 + 3] / 40 - 1);

export const PC_PER_LY = 0.306601;
export const AU_PER_PC = 206264.806;

export function fmtDist(pc: number): string {
  if (!Number.isFinite(pc)) return "–";
  const ly = pc / PC_PER_LY;
  if (pc < 0.01) return `${(pc * AU_PER_PC).toFixed(pc * AU_PER_PC < 10 ? 2 : 0)} AU`;
  if (pc < 1000) return `${pc < 10 ? pc.toFixed(2) : pc.toFixed(1)} pc · ${ly < 100 ? ly.toFixed(1) : ly.toFixed(0)} ly`;
  return `${(pc / 1000).toFixed(2)} kpc · ${(ly / 1000).toFixed(1)} kly`;
}

// ---------------------------------------------------------------------------
// Survey sectors: the neighbourhood (<= 100 pc) cut into shells x longitude x latitude.
// Each sector reports how much of it is still uncharted.
// ---------------------------------------------------------------------------
export const SHELLS = [0, 10, 25, 50, 100];
const L_BINS = 8;
const B_EDGES = [-90, -30, 0, 30, 90];

export interface Sector {
  id: number; name: string; shell: [number, number]; l: [number, number]; b: [number, number];
  total: number; unclassified: number; lowConf: number; spherex: number; catalogue: number; predicted: number;
  centroid: [number, number, number]; frontier: number;
}

const SYL = ["ka", "vor", "ith", "ra", "sel", "mon", "dur", "an", "ve", "quor", "lis", "tal", "zen", "or", "bri", "hal", "nym", "ex", "cor", "ul"];
const SUFFIX = ["Reach", "Drift", "Expanse", "Verge", "Hollow", "March", "Shoal", "Deep", "Span", "Basin"];
function codename(id: number): string {
  let h = (id * 2654435761) >>> 0;
  const pick = (arr: string[]) => { const v = arr[h % arr.length]; h = (h * 1103515245 + 12345) >>> 0; return v; };
  const w = pick(SYL) + pick(SYL) + (h % 3 === 0 ? pick(SYL) : "");
  return `${w[0].toUpperCase()}${w.slice(1)} ${pick(SUFFIX)}`;
}

export function sectorOf(x: number, y: number, z: number): number {
  const r = Math.hypot(x, y, z);
  let s = -1;
  for (let k = 0; k < SHELLS.length - 1; k++) if (r >= SHELLS[k] && r < SHELLS[k + 1]) s = k;
  if (s < 0) return -1;
  const l = ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
  const b = (Math.asin(Math.max(-1, Math.min(1, z / Math.max(r, 1e-9)))) * 180) / Math.PI;
  const li = Math.min(L_BINS - 1, Math.floor(l / (360 / L_BINS)));
  let bi = 0;
  while (bi < B_EDGES.length - 2 && b >= B_EDGES[bi + 1]) bi++;
  return (s * L_BINS + li) * (B_EDGES.length - 1) + bi;
}

export function computeSectors(st: StarBuffers): { sectors: Sector[]; sectorIndex: Int16Array } {
  const nB = B_EDGES.length - 1;
  const nS = (SHELLS.length - 1) * L_BINS * nB;
  const sectors: Sector[] = [];
  for (let id = 0; id < nS; id++) {
    const bi = id % nB, li = Math.floor(id / nB) % L_BINS, s = Math.floor(id / nB / L_BINS);
    sectors.push({ id, name: codename(id + 17), shell: [SHELLS[s], SHELLS[s + 1]], l: [li * 45, li * 45 + 45], b: [B_EDGES[bi], B_EDGES[bi + 1]],
      total: 0, unclassified: 0, lowConf: 0, spherex: 0, catalogue: 0, predicted: 0, centroid: [0, 0, 0], frontier: 0 });
  }
  const idx = new Int16Array(st.n).fill(-1);
  for (let i = 0; i < st.n; i++) {
    const x = st.xyz[i * 3], y = st.xyz[i * 3 + 1], z = st.xyz[i * 3 + 2];
    const k = sectorOf(x, y, z);
    if (k < 0) continue;
    idx[i] = k;
    const sec = sectors[k];
    sec.total++;
    sec.centroid[0] += x; sec.centroid[1] += y; sec.centroid[2] += z;
    const src = st.attrs[i * 4] >> 4, fl = st.attrs[i * 4 + 1];
    if (src === 0) sec.unclassified++;
    else if (src === 1) { sec.predicted++; if (fl & FLAG.low_confidence) sec.lowConf++; }
    else if (src === 2) sec.catalogue++;
    else sec.spherex++;
  }
  for (const s of sectors) {
    if (s.total) s.centroid = [s.centroid[0] / s.total, s.centroid[1] / s.total, s.centroid[2] / s.total];
    s.frontier = s.total ? (s.unclassified + s.lowConf + 0.5 * s.predicted) / s.total : 0;
  }
  return { sectors, sectorIndex: idx };
}

// ---------------------------------------------------------------------------
// Backend
// ---------------------------------------------------------------------------
export interface SpherexSpectrum {
  i?: number; name: string | null; status: "ok" | "saturated" | "too_faint" | "no_data"; ra: number; dec: number;
  n_frames: number; n_good: number; releases: string[]; detectors: number[]; n_epochs: number; mjd_range: [number | null, number | null];
  spectrum: { w: number; f: number; e: number | null; n: number }[];
  classification: null | { class_id: number | null; label: string; confidence: number; t_color_k?: number; chi2_red?: number;
    snr_median?: number; features?: string[]; evidence: string[]; bb_model?: number[]; indices?: Record<string, number | null>; method?: string };
  runtime_s?: number;
}

export interface ObjectRecord {
  i: number; name: string | null; gaia_dr3_id: string | null; ra: number | null; dec: number | null; parallax_mas: number | null;
  distance_pc: number | null; pmra: number | null; pmdec: number | null; radial_velocity: number | null; v_tan_km_s: number | null;
  gmag: number | null; bp_rp: number | null; teff_gspphot: number | null; ruwe: number | null; otype: string | null; sp_type: string | null;
  class_id: number; class_source: ClassSource; confidence: number | null; flags: number; abs_g?: number;
  system: PlanetSystem | null; spherex: SpherexSpectrum | null;
}

export async function fetchObject(i: number): Promise<ObjectRecord> {
  const r = await fetch(`/api/galaxy/object/${i}`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return r.json();
}

export async function measureSpectrum(i: number, signal?: AbortSignal): Promise<SpherexSpectrum> {
  const r = await fetch(`/api/galaxy/spectrum/${i}`, { signal });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return r.json();
}

export async function fetchSurvey(): Promise<{ i: number; status: string; class_id: number | null }[]> {
  const r = await fetch("/api/galaxy/survey");
  if (!r.ok) return [];
  return (await r.json()).objects;
}

/** ICRS -> Galactic rotation (Hipparcos / ESA 1997); its transpose maps Galactic back to ICRS. */
const ICRS_TO_GAL = [
  [-0.0548755604162154, -0.873437090234885, -0.4838350155487132],
  [0.4941094278755837, -0.4448296299600112, 0.7469822444972189],
  [-0.8676661490190047, -0.1980763734312015, 0.4559837761750669],
];

/** Sky position (ICRS RA, Dec in degrees) of a heliocentric Galactic cartesian point. */
export function galToRaDec(x: number, y: number, z: number): [number, number] {
  const M = ICRS_TO_GAL;
  const ex = M[0][0] * x + M[1][0] * y + M[2][0] * z;
  const ey = M[0][1] * x + M[1][1] * y + M[2][1] * z;
  const ez = M[0][2] * x + M[1][2] * y + M[2][2] * z;
  const r = Math.hypot(ex, ey, ez) || 1;
  const ra = (Math.atan2(ey, ex) * 180) / Math.PI;
  return [(ra + 360) % 360, (Math.asin(Math.max(-1, Math.min(1, ez / r))) * 180) / Math.PI];
}
