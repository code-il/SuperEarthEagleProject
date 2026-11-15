import { describe, expect, it } from "vitest";
import { smoothFactor, upsamplePlane } from "./render";
import type { ImagePlane } from "../types";

const plane = (w: number, h: number, f: (x: number, y: number) => number, mask?: Uint8Array): ImagePlane => {
  const data = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) data[y * w + x] = f(x, y);
  return { width: w, height: h, data, mask, wcs: {} as ImagePlane["wcs"] };
};

describe("bicubic display upsampling", () => {
  it("picks a factor that keeps the result near 512 px", () => {
    expect(smoothFactor(30, 30)).toBe(8);
    expect(smoothFactor(120, 100)).toBe(4);
    expect(smoothFactor(1000, 800)).toBe(1);
  });

  it("preserves a flat field and a linear ramp", () => {
    const flat = upsamplePlane(plane(6, 5, () => 3), 4);
    expect(flat.width).toBe(24);
    expect(flat.height).toBe(20);
    expect([...flat.data].every((v) => Math.abs(v - 3) < 1e-5)).toBe(true);
    const ramp = upsamplePlane(plane(8, 4, (x) => x), 2);
    // Output pixel X samples source coordinate (X + 0.5) / 2 - 0.5 (interior pixels: exact for a ramp).
    expect(ramp.data[1 * 16 + 7]).toBeCloseTo(3.25, 5);
  });

  it("fills an isolated masked pixel from its neighbours, never from its own bad value", () => {
    const m = new Uint8Array(25);
    m[12] = 1;
    const up = upsamplePlane(plane(5, 5, (x, y) => (x === 2 && y === 2 ? 1e6 : 1), m), 2);
    expect([...up.data].every((x) => Math.abs(x - 1) < 1e-5)).toBe(true);
  });

  it("keeps a large masked area invalid and does not bleed it into neighbours", () => {
    const m = new Uint8Array(36);
    for (const i of [14, 15, 20, 21]) m[i] = 1; // 2x2 block in a 6x6 plane
    const up = upsamplePlane(plane(6, 6, (x, y) => (m[y * 6 + x] ? 1e6 : 1), m), 2);
    const v = [...up.data];
    expect(v.filter((x) => Number.isNaN(x)).length).toBe(16);
    expect(v.filter(Number.isFinite).every((x) => Math.abs(x - 1) < 1e-5)).toBe(true);
  });
});
