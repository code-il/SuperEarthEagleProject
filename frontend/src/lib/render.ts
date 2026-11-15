/**
 * Display rendering: science values -> RGBA pixels.
 *
 * Everything here is display-only. The Float32 science array is never modified;
 * stretch, brightness, contrast, colormap and invert only change the colours.
 */
import type { Colormap, DisplaySettings, ImagePlane, Stretch } from "../types";

export type Rgb = [number, number, number];

// Polynomial fits to the matplotlib perceptual colormaps (after M. Zucker, CC0).
const POLY: Record<"viridis" | "inferno" | "magma", number[][]> = {
  viridis: [
    [0.2777273272234177, 0.005407344544966578, 0.3340998053353061],
    [0.1050930431085774, 1.404613529898575, 1.384590162594685],
    [-0.3308618287255563, 0.214847559468213, 0.09509516302823659],
    [-4.634230498983486, -5.799100973351585, -19.33244095627987],
    [6.228269936347081, 14.17993336680509, 56.69055260068105],
    [4.776384997670288, -13.74514537774601, -65.35303263337234],
    [-5.435455855934631, 4.645852612178535, 26.3124352495832],
  ],
  inferno: [
    [0.0002189403691192265, 0.001651004631001012, -0.01948089843709184],
    [0.1065134194856116, 0.5639564367884091, 3.932712388889277],
    [11.60249308247187, -3.972853965665698, -15.9423941062914],
    [-41.70399613139459, 17.43639888205313, 44.35414519872813],
    [77.162935699427, -33.40235894210092, -81.80730925738993],
    [-71.31942824499214, 32.62606426397723, 73.20951985803202],
    [25.13112622477341, -12.24266895238567, -23.07032500287172],
  ],
  magma: [
    [-0.002136485053939582, -0.000749655052795221, -0.005386127855323933],
    [0.2516605407371642, 0.6775232436837668, 2.494026599312351],
    [8.353717279216625, -3.577719514958484, 0.3144679030132573],
    [-27.66873308576866, 14.26473078096533, -13.64921318813922],
    [52.17613981234068, -27.94360607168351, 12.94416944238394],
    [-50.76852536473588, 29.04658282127291, 4.23415299384598],
    [18.65570506591883, -11.48977351997711, -5.601961508734096],
  ],
};

const BLUE: Rgb = [33, 102, 172];
const WHITE: Rgb = [247, 247, 247];
const RED: Rgb = [178, 24, 43];

function clamp01(x: number): number {
  return x < 0 ? 0 : x > 1 ? 1 : x;
}

export function colormapRgb(name: Colormap, t: number): Rgb {
  t = clamp01(t);
  if (name === "gray") {
    const g = Math.round(t * 255);
    return [g, g, g];
  }
  if (name === "diverging") {
    const [a, b, u] = t < 0.5 ? [BLUE, WHITE, t * 2] : [WHITE, RED, (t - 0.5) * 2];
    return [0, 1, 2].map((i) => Math.round(a[i] + (b[i] - a[i]) * u)) as Rgb;
  }
  const c = POLY[name];
  const out: number[] = [0, 0, 0];
  for (let i = 0; i < 3; i++) {
    let v = c[6][i];
    for (let k = 5; k >= 0; k--) v = c[k][i] + t * v;
    out[i] = Math.round(clamp01(v) * 255);
  }
  return out as Rgb;
}

const lutCache = new Map<Colormap, Uint8ClampedArray>();

/** 256-entry RGB lookup table. */
export function colormapLut(name: Colormap): Uint8ClampedArray {
  let lut = lutCache.get(name);
  if (!lut) {
    lut = new Uint8ClampedArray(256 * 3);
    for (let i = 0; i < 256; i++) lut.set(colormapRgb(name, i / 255), i * 3);
    lutCache.set(name, lut);
  }
  return lut;
}

/** Stretch a normalised value t in [0, 1]. Same definitions as the backend PNGs. */
export function stretchValue(stretch: Stretch, t: number): number {
  t = clamp01(t);
  switch (stretch) {
    case "linear": return t;
    case "sqrt": return Math.sqrt(t);
    case "log": return Math.log10(1 + 1000 * t) / Math.log10(1001);
    case "asinh": return Math.asinh(t / 0.1) / Math.asinh(10);
  }
}

/** Apply brightness and contrast after the stretch (display only). */
export function adjust(s: number, brightness: number, contrast: number): number {
  return clamp01((s - 0.5) * contrast + 0.5 + brightness);
}

/** Percentiles of finite, unmasked values (subsampled for speed). */
export function percentiles(values: ArrayLike<number>[], masks: (Uint8Array | undefined)[], ps: number[], maxSamples = 250_000): number[] {
  let total = 0;
  for (const v of values) total += v.length;
  const step = Math.max(1, Math.floor(total / maxSamples));
  const sample: number[] = [];
  values.forEach((v, k) => {
    const m = masks[k];
    for (let i = 0; i < v.length; i += step) {
      const x = v[i];
      if (Number.isFinite(x) && !(m && m[i])) sample.push(x);
    }
  });
  if (!sample.length) return ps.map(() => NaN);
  sample.sort((a, b) => a - b);
  return ps.map((p) => {
    const idx = clamp01(p / 100) * (sample.length - 1);
    const lo = Math.floor(idx), hi = Math.ceil(idx);
    return sample[lo] + (sample[hi] - sample[lo]) * (idx - lo);
  });
}

export function limitsFor(planes: ImagePlane[], pct: [number, number], diverging = false, center = 0): [number, number] {
  if (diverging) {
    const devs = planes.map((p) => p.data.map((v) => Math.abs(v - center)));
    const [hi] = percentiles(devs, planes.map((p) => p.mask), [pct[1]]);
    const h = Number.isFinite(hi) && hi > 0 ? hi : 1;
    return [center - h, center + h];
  }
  const [lo, hi] = percentiles(planes.map((p) => p.data), planes.map((p) => p.mask), pct);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  return hi > lo ? [lo, hi] : [lo, lo + 1];
}

/**
 * Render a plane into RGBA, flipped so north (FITS +y) is at the top.
 * NaN or masked pixels become transparent.
 */
export function renderPlane(plane: ImagePlane, limits: [number, number], d: DisplaySettings, diverging = false): ImageData {
  const { width: w, height: h, data, mask } = plane;
  const out = new Uint8ClampedArray(w * h * 4);
  const cmap: Colormap = diverging && d.colormap !== "gray" ? "diverging" : d.colormap;
  const lut = colormapLut(cmap);
  const [lo, hi] = limits;
  const span = hi - lo || 1;
  // Diverging data uses a symmetric stretch so zero stays white.
  const sym = diverging;
  for (let y = 0; y < h; y++) {
    const srcRow = (h - 1 - y) * w;
    const dstRow = y * w;
    for (let x = 0; x < w; x++) {
      const v = data[srcRow + x];
      const o = (dstRow + x) * 4;
      if (!Number.isFinite(v) || (mask && mask[srcRow + x])) {
        out[o + 3] = 0;
        continue;
      }
      let t = (v - lo) / span;
      if (sym) {
        const c = 2 * t - 1; // -1..1
        const s = stretchValue(d.stretch, Math.abs(c));
        t = 0.5 + 0.5 * Math.sign(c) * s;
      } else t = stretchValue(d.stretch, t);
      t = adjust(t, d.brightness, d.contrast);
      if (d.invert) t = 1 - t;
      const k = Math.round(t * 255) * 3;
      out[o] = lut[k];
      out[o + 1] = lut[k + 1];
      out[o + 2] = lut[k + 2];
      out[o + 3] = 255;
    }
  }
  return new ImageData(out, w, h);
}

export const DEFAULT_DISPLAY: DisplaySettings = {
  stretch: "asinh",
  colormap: "inferno",
  invert: false,
  brightness: 0,
  contrast: 1,
  percentiles: [0.5, 99.5],
  limitsMode: "channel",
  interpolation: "smooth",
  plane: "SCI",
};

/** Catmull-Rom kernel weights for fractional offset t (taps at -1, 0, 1, 2). */
function cubicWeights(t: number): [number, number, number, number] {
  const t2 = t * t, t3 = t2 * t;
  return [(-t3 + 2 * t2 - t) / 2, (3 * t3 - 5 * t2 + 2) / 2, (-3 * t3 + 4 * t2 + t) / 2, (t3 - t2) / 2];
}

/** Upsampling factor for display: about 512 px across the result, between 1 and 8. */
export function smoothFactor(w: number, h: number): number {
  return Math.max(1, Math.min(8, Math.floor(512 / Math.max(w, h, 1))));
}

/**
 * Bicubic (Catmull-Rom) resampling of a plane by an integer factor, for display only.
 * Isolated masked / NaN pixels (cosmic-ray and hot-pixel hits, single pixels or pairs: at least 6 of 8 neighbours valid)
 * are first filled with the mean of their valid neighbours; larger masked areas stay invalid and are
 * left out of their neighbours' kernels, so bad pixels do not bleed. The science array is not changed
 * (readouts still report "masked"), and the WCS is not updated: callers draw the result over the
 * original pixel footprint.
 */
export function upsamplePlane(plane: ImagePlane, k: number): ImagePlane {
  if (k <= 1) return plane;
  const { width: w, height: h, mask } = plane;
  const W = w * k, H = h * k;
  const out = new Float32Array(W * H);
  const valid0 = (i: number) => Number.isFinite(plane.data[i]) && !(mask && mask[i]);
  const data = Float32Array.from(plane.data);
  const good = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) good[i] = valid0(i) ? 1 : 0;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = y * w + x;
    if (good[i]) continue;
    let s = 0, n = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const xx = x + dx, yy = y + dy;
      if ((dx || dy) && xx >= 0 && yy >= 0 && xx < w && yy < h && valid0(yy * w + xx)) { s += plane.data[yy * w + xx]; n++; }
    }
    if (n >= 6) { data[i] = s / n; good[i] = 1; }
  }
  const ok = (i: number) => good[i] === 1;
  const wy: [number, number, number, number][] = [], wx: [number, number, number, number][] = [];
  const iy0: number[] = [], ix0: number[] = [];
  for (let Y = 0; Y < H; Y++) { const s = (Y + 0.5) / k - 0.5, f = Math.floor(s); iy0.push(f); wy.push(cubicWeights(s - f)); }
  for (let X = 0; X < W; X++) { const s = (X + 0.5) / k - 0.5, f = Math.floor(s); ix0.push(f); wx.push(cubicWeights(s - f)); }
  for (let Y = 0; Y < H; Y++) {
    const fy = iy0[Y], ky = wy[Y];
    for (let X = 0; X < W; X++) {
      const fx = ix0[X], kx = wx[X];
      // The nearest source pixel decides validity.
      const ny = Math.min(h - 1, Math.max(0, Math.round((Y + 0.5) / k - 0.5)));
      const nx = Math.min(w - 1, Math.max(0, Math.round((X + 0.5) / k - 0.5)));
      if (!ok(ny * w + nx)) { out[Y * W + X] = NaN; continue; }
      let acc = 0, wsum = 0;
      for (let j = 0; j < 4; j++) {
        const yy = Math.min(h - 1, Math.max(0, fy - 1 + j));
        for (let i = 0; i < 4; i++) {
          const xx = Math.min(w - 1, Math.max(0, fx - 1 + i));
          const idx = yy * w + xx;
          if (!ok(idx)) continue;
          const wt = ky[j] * kx[i];
          acc += wt * data[idx];
          wsum += wt;
        }
      }
      out[Y * W + X] = wsum > 0.5 ? acc / wsum : data[ny * w + nx];
    }
  }
  return { ...plane, width: W, height: H, data: out, mask: undefined };
}
