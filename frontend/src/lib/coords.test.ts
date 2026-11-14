import { describe, expect, it } from "vitest";
import { CoordinateInputError, formatDecDms, formatRaHms, parseDec, parseQuery, parseRa, validateFov } from "./coords";

describe("parseQuery", () => {
  it("reads decimal degrees", () => {
    expect(parseQuery("269.452 4.693")).toMatchObject({ kind: "coordinates", ra: 269.452, dec: 4.693 });
    expect(parseQuery("269.452, -4.693")).toMatchObject({ ra: 269.452, dec: -4.693 });
  });
  it("reads sexagesimal (RA in hours)", () => {
    const q = parseQuery("08h55m10.831s -07d14m42.53s");
    expect(q.kind).toBe("coordinates");
    if (q.kind !== "coordinates") return;
    expect(q.ra).toBeCloseTo(133.7951292, 6);
    expect(q.dec).toBeCloseTo(-7.2451472, 6);
    const c = parseQuery("08:55:10.831 -07:14:42.53");
    expect(c).toMatchObject({ kind: "coordinates" });
    const s = parseQuery("08 55 10.831 -07 14 42.53");
    expect(s.kind === "coordinates" && s.ra).toBeCloseTo(133.7951292, 6);
  });
  it("treats other text as a name", () => {
    expect(parseQuery("Barnard's Star")).toMatchObject({ kind: "name", name: "Barnard's Star" });
    expect(parseQuery("WISE J085510.83-071442.5")).toMatchObject({ kind: "name" });
    expect(parseQuery("eps Ind B")).toMatchObject({ kind: "name" });
  });
  it("rejects invalid coordinates", () => {
    expect(() => parseQuery("400 10")).toThrow(CoordinateInputError);
    expect(() => parseQuery("10 95")).toThrow(CoordinateInputError);
    expect(() => parseQuery("25h00m00s +10d00m00s")).toThrow(CoordinateInputError);
    expect(() => parseQuery("12:61:00 +10:00:00")).toThrow(CoordinateInputError);
    expect(() => parseQuery("   ")).toThrow(CoordinateInputError);
    expect(() => parseQuery("12.5")).toThrow(CoordinateInputError);
  });
});

describe("parse/format round trip", () => {
  it("RA and Dec", () => {
    expect(parseRa(formatRaHms(269.452, 3))).toBeCloseTo(269.452, 5);
    expect(parseDec(formatDecDms(-7.2451472, 2))).toBeCloseTo(-7.2451472, 5);
    expect(formatDecDms(-0.5)).toBe("−00°30′00.0″");
  });
  it("validates FOV", () => {
    expect(validateFov(0.1)).toBe(0.1);
    expect(() => validateFov(5)).toThrow();
    expect(() => validateFov(0.001)).toThrow();
  });
});
