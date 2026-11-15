import { describe, expect, it } from "vitest";
import { autoReference, nearestInChannel, nearestVisible, visitIds, type FrameLike } from "./selection";
import { buildUrl, parseUrl } from "./urlState";
import { propagate } from "./catalog";

const f = (i: number, mjd: number, wl: number, epoch: number, det = 6, ch = 90): FrameLike => ({
  seriesIndex: i, observation_id: `o${i}`, mjd, wavelength: wl, detector: det, epoch, channel: { index: ch },
});
const frames = [f(0, 0, 4.5, 0), f(1, 0.01, 4.9, 0), f(2, 180, 4.52, 1), f(3, 181, 4.9, 1, 6, 99), f(4, 365, 4.49, 2)];

describe("frame selection", () => {
  it("auto reference: other visit, closest wavelength, furthest in time on ties", () => {
    expect(autoReference(frames, frames[0])?.seriesIndex).toBe(4); // 4.49 (|0.01|) beats 4.52 (|0.02|)
    expect(autoReference(frames, frames[1])?.seriesIndex).toBe(3);
    expect(autoReference([frames[0]], frames[0])).toBeUndefined();
  });
  it("prefers the same detector", () => {
    const mix = [f(0, 0, 4.5, 0, 6), f(1, 180, 4.5, 1, 5), f(2, 365, 4.7, 2, 6)];
    expect(autoReference(mix, mix[0])?.seriesIndex).toBe(2);
  });
  it("channel, visit and nearest helpers", () => {
    expect(nearestInChannel(frames, frames[2], 99)?.seriesIndex).toBe(3);
    expect(visitIds(frames, frames[2])).toEqual(["o2", "o3"]);
    expect(nearestVisible(frames, 170)?.seriesIndex).toBe(2);
  });
});

describe("URL state", () => {
  it("parses the documented example", () => {
    const u = parseUrl(new URLSearchParams("ra=269.452&dec=4.693&fov=0.1"));
    expect(u.params).toMatchObject({ ra: 269.452, dec: 4.693, fov: 0.1, detectors: [6] });
    expect(u.error).toBeNull();
  });
  it("accepts sexagesimal, names, detectors, mode", () => {
    const u = parseUrl(new URLSearchParams("ra=08h55m10.831s&dec=-07d14m42.53s&det=1,D2&mode=blink"));
    expect(u.params?.ra).toBeCloseTo(133.7951, 3);
    expect(u.params?.detectors).toEqual([1, 2]);
    expect(u.mode).toBe("blink");
    expect(parseUrl(new URLSearchParams("q=Barnard's Star")).params?.q).toBe("Barnard's Star");
  });
  it("reports bad input instead of loading", () => {
    const u = parseUrl(new URLSearchParams("ra=500&dec=0"));
    expect(u.params).toBeNull();
    expect(u.error).toMatch(/RA/);
    expect(parseUrl(new URLSearchParams("ra=1&dec=1&fov=9")).error).toMatch(/Field of view/);
  });
  it("round-trips", () => {
    const p = { ra: 269.452, dec: 4.693, fov: 0.1, detectors: [6] };
    const url = buildUrl(p, { mode: "difference" });
    expect(url).toBe("/explore?ra=269.452&dec=4.693&fov=0.1&mode=difference");
    expect(parseUrl(new URLSearchParams(url.split("?")[1])).params).toMatchObject(p);
  });
});

describe("proper-motion propagation", () => {
  it("moves Barnard's Star about 10.4 arcsec/yr north", () => {
    const s = { ra: 269.4485, dec: 4.7394, ref_mjd: 57388.5, pm_ra_cosdec_mas_yr: -801.55, pm_dec_mas_yr: 10362.39 };
    const [ra, dec] = propagate(s, 57388.5 + 365.25 * 10);
    expect((dec - s.dec) * 3600).toBeCloseTo(103.6, 1);
    expect(ra).toBeLessThan(s.ra);
    expect(propagate({ ra: 1, dec: 2, ref_mjd: null, pm_ra_cosdec_mas_yr: null, pm_dec_mas_yr: null }, 60000)).toEqual([1, 2]);
    // Suspect CatWISE motions (halo of a saturated star) are not applied.
    expect(propagate({ ...s, pm_suspect: true }, 57388.5 + 3650)).toEqual([s.ra, s.dec]);
  });
});
