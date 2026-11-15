import { describe, expect, it } from "vitest";
import { covers, trackEnd, trackFromChunk, trackPosition } from "./nbodyTrack";

// A circular orbit sampled 20 times per period, as the backend sends it.
const P = 3, a = 0.04, w = (2 * Math.PI) / P, step = P / 20, n = 61;
const pos: number[] = [], vel: number[] = [];
for (let k = 0; k < n; k++) {
  const t = k * step;
  pos.push(a * Math.cos(w * t), a * Math.sin(w * t), 0);
  vel.push(-a * w * Math.sin(w * t), a * w * Math.cos(w * t), 0);
}
const track = trackFromChunk({ host: "S", status: "ok", notes: [], t0: 2460000, step, n, planets: [{ name: "S b", pos, vel }] });

describe("nbodyTrack", () => {
  it("interpolates between samples to the theoretical (2pi/20)^4/384 = 2.5e-5 of the orbit", () => {
    for (const dt of [0.01, 0.37, 1.234, 2.9, 8.05]) {
      const [x, y] = trackPosition(track, "S b", 2460000 + dt)!;
      expect(Math.hypot(x - a * Math.cos(w * dt), y - a * Math.sin(w * dt)) / a).toBeLessThan(3e-5);
    }
  });

  it("reports coverage and refuses dates outside it", () => {
    expect(trackEnd(track)).toBeCloseTo(2460000 + 60 * step, 9);
    expect(covers(track, 2460000 + 5)).toBe(true);
    expect(covers(track, 2459999)).toBe(false);
    expect(trackPosition(track, "S b", 2460000 + 100)).toBeNull();
    expect(trackPosition(track, "other", 2460001)).toBeNull();
  });
});
