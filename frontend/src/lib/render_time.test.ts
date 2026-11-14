import { describe, expect, it } from "vitest";
import { colormapRgb, limitsFor, renderPlane, stretchValue, DEFAULT_DISPLAY } from "./render";
import { frameDurationMs, formatDuration, groupByGap, isoToMjd, mjdToDate, nearestIndex } from "./time";
import { timelinePositions } from "../components/Timeline";
import type { ImagePlane } from "../types";

const wcs = { CTYPE1: "RA---TAN", CTYPE2: "DEC--TAN", CRPIX1: 1, CRPIX2: 1, CRVAL1: 0, CRVAL2: 0 };

describe("display rendering never alters data", () => {
  it("stretches map 0->0 and 1->1", () => {
    for (const s of ["linear", "sqrt", "log", "asinh"] as const) {
      expect(stretchValue(s, 0)).toBeCloseTo(0, 6);
      expect(stretchValue(s, 1)).toBeCloseTo(1, 6);
      expect(stretchValue(s, 0.3)).toBeGreaterThan(0);
    }
  });
  it("diverging colormap is white at the centre", () => {
    const [r, g, b] = colormapRgb("diverging", 0.5);
    expect(Math.min(r, g, b)).toBeGreaterThan(240);
    expect(colormapRgb("gray", 1)).toEqual([255, 255, 255]);
  });
  it("flips rows so north is up, masks become transparent, input is untouched", () => {
    // 2x2: row 0 (bottom) = [0, 1], row 1 (top) = [2, NaN]
    const data = new Float32Array([0, 1, 2, NaN]);
    const copy = Float32Array.from(data);
    const plane: ImagePlane = { width: 2, height: 2, data, mask: new Uint8Array([0, 1, 0, 0]), wcs };
    const img = renderPlane(plane, [0, 2], { ...DEFAULT_DISPLAY, stretch: "linear", colormap: "gray" });
    const px = (x: number, y: number) => Array.from(img.data.slice((y * 2 + x) * 4, (y * 2 + x) * 4 + 4));
    expect(px(0, 0)).toEqual([255, 255, 255, 255]); // top-left = FITS (0,1) = 2 = max
    expect(px(1, 0)[3]).toBe(0); // NaN
    expect(px(0, 1)).toEqual([0, 0, 0, 255]); // bottom-left = 0
    expect(px(1, 1)[3]).toBe(0); // masked
    expect(Array.from(data)).toEqual(Array.from(copy));
    const inv = renderPlane(plane, [0, 2], { ...DEFAULT_DISPLAY, stretch: "linear", colormap: "gray", invert: true });
    expect(Array.from(inv.data.slice(0, 3))).toEqual([0, 0, 0]);
  });
  it("limits ignore masked and NaN pixels; diverging limits are symmetric", () => {
    const plane: ImagePlane = { width: 5, height: 1, data: new Float32Array([1, 2, 3, 1000, NaN]), mask: new Uint8Array([0, 0, 0, 1, 0]), wcs };
    expect(limitsFor([plane], [0, 100])).toEqual([1, 3]);
    const d: ImagePlane = { width: 3, height: 1, data: new Float32Array([-1, 0.5, 4]), wcs };
    const [lo, hi] = limitsFor([d], [0, 100], true);
    expect(lo).toBeCloseTo(-hi);
    expect(hi).toBeCloseTo(4);
  });
});

describe("time helpers (irregular sampling)", () => {
  it("converts MJD <-> dates", () => {
    expect(mjdToDate(61110.1).toISOString().slice(0, 10)).toBe("2026-03-11");
    expect(isoToMjd("2026-08-16T15:07:12Z")).toBeCloseTo(61268.63, 2);
  });
  it("groups visits by gaps and finds nearest frames", () => {
    const m = [60902.8, 60902.9, 60910, 61085, 61086, 61268];
    expect(groupByGap(m, 20)).toEqual([{ start: 0, end: 2 }, { start: 3, end: 4 }, { start: 5, end: 5 }]);
    expect(nearestIndex(m, 61000)).toBe(3);
    expect(nearestIndex(m, 60000)).toBe(0);
  });
  it("timeline positions follow real time and are monotonic", () => {
    const m = [0, 0.001, 10, 180, 181, 365];
    for (const axis of ["visits", "linear"] as const) {
      const { x, groups } = timelinePositions(m, axis, 20);
      expect(groups).toHaveLength(3);
      for (let i = 1; i < x.length; i++) expect(x[i]).toBeGreaterThanOrEqual(x[i - 1]);
      expect(x[0]).toBeCloseTo(0);
    }
    const lin = timelinePositions(m, "linear").x;
    expect(lin[3]).toBeCloseTo(180 / 365);
    // within a visit, spacing is proportional to time (0.001 d vs 10 d)
    const vis = timelinePositions(m, "visits").x;
    expect((vis[1] - vis[0]) / (vis[2] - vis[0])).toBeCloseTo(0.0001, 4);
  });
  it("proportional timing lengthens frames before long gaps", () => {
    const m = [0, 0.001, 180];
    expect(frameDurationMs(m, 0, 2, "uniform")).toBe(500);
    expect(frameDurationMs(m, 1, 2, "proportional")).toBeGreaterThan(frameDurationMs(m, 0, 2, "proportional"));
    expect(formatDuration(0.0015)).toBe("2.2 min");
    expect(formatDuration(182)).toBe("182.0 d");
    expect(formatDuration(730)).toBe("2.00 yr");
  });
});
