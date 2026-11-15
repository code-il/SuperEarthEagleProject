import { describe, expect, it } from "vitest";
import type { Planet } from "./galaxyData";
import { DAYS_PER_SECOND, meanAnomaly, orbitFor, phaseSigma, positionAt, positionAtM, solveKepler } from "./orbits";

const planet = (o: Partial<Planet>): Planet => ({ name: "X b", a_au: null, period_d: null, radius_re: null, mass_me: null,
  teq_k: null, ecc: null, method: "Transit", year: "2020", ...o });

describe("orbits", () => {
  it("solves Kepler's equation", () => {
    for (const e of [0, 0.2, 0.6, 0.9]) for (const M of [-3, -1, 0.1, 1, 2.5, 3.1]) {
      const E = solveKepler(M, e);
      expect(E - e * Math.sin(E)).toBeCloseTo(M, 10);
    }
  });

  it("runs at one fixed rate: Mercury takes 88 real seconds", () => {
    expect(DAYS_PER_SECOND).toBe(1);
    const mercury = orbitFor(planet({ name: "Mercury", period_d: 87.969, a_au: 0.387, ecc: 0.2056, t_peri: 2460000.5, omega_deg: 29.1 }), 1)!;
    const p0 = positionAt(mercury, 2460010), p1 = positionAt(mercury, 2460010 + 87.969 * DAYS_PER_SECOND * 1);
    expect(p1[0]).toBeCloseTo(p0[0], 9);
    expect(p1[1]).toBeCloseTo(p0[1], 9);
  });

  it("puts a transiting planet between its star and us at mid-transit", () => {
    const o = orbitFor(planet({ period_d: 3, a_au: 0.04, ecc: 0.3, omega_deg: 40, t_transit: 2459000, t_transit_err: 0.001, period_err: 1e-5 }), 0.8)!;
    const [x, y] = positionAt(o, 2459000 + 5 * 3); // five orbits later, same phase
    expect(Math.abs(x)).toBeLessThan(1e-9);
    expect(y).toBeGreaterThan(0); // towards the observer
  });

  it("obeys Kepler's second law (faster at periastron)", () => {
    const o = orbitFor(planet({ period_d: 100, a_au: 0.4, ecc: 0.5, t_peri: 2459000 }), 1)!;
    const step = (M: number) => Math.hypot(...positionAtM(o, M + 0.01).map((v, k) => v - positionAtM(o, M)[k]) as [number, number]);
    // speed at periastron / apoastron = (1 + e) / (1 - e) = 3 for e = 0.5
    expect(step(0) / step(Math.PI)).toBeCloseTo(3, 2);
  });

  it("derives a or P with Kepler's third law only from real inputs", () => {
    const o = orbitFor(planet({ period_d: 365.25 }), 1)!;
    expect(o.a).toBeCloseTo(1, 6);
    expect(o.a_source).toBe("kepler3");
    expect(orbitFor(planet({ period_d: 10 }), null)).toBeNull(); // no star mass: nothing invented
    expect(orbitFor(planet({}), 1)).toBeNull();
  });

  it("grows the phase uncertainty with time and flags unknown phases", () => {
    const o = orbitFor(planet({ period_d: 2, a_au: 0.02, t_transit: 2459000, t_transit_err: 0.001, period_err: 1e-4 }), 0.5)!;
    expect(phaseSigma(o, 2459000)).toBeCloseTo((2 * Math.PI / 2) * 0.001, 8);
    expect(phaseSigma(o, 2459000 + 2000)).toBeGreaterThan(phaseSigma(o, 2459100));
    const u = orbitFor(planet({ period_d: 2, a_au: 0.02 }), 0.5)!;
    expect(u.phase).toBe("unknown");
    expect(phaseSigma(u, 2459000)).toBe(Infinity);
    expect(Number.isFinite(meanAnomaly(u, 2459000))).toBe(true);
  });
});
