import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { findHdu, headerWcs, parseHeader, readFits } from "./fits";
import { makeTan, northAngle, pix2world, pixelScaleArcsec, separationArcsec, world2pix } from "./wcs";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const expected = JSON.parse(readFileSync(here("../test/processed_D6.expected.json"), "utf8"));
const buf = readFileSync(here("../test/processed_D6.fits"));
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;

function card(s: string) {
  return s.padEnd(80, " ");
}

describe("FITS reader (real processed SPHEREx product vs astropy)", () => {
  const hdus = readFits(ab);
  it("finds every extension", () => {
    expect(hdus.map((h) => h.name)).toEqual(expected.names);
  });
  it("reads SCI values, NaNs and shape", () => {
    const sci = findHdu(hdus, "SCI")!;
    expect([sci.height, sci.width]).toEqual(expected.shape);
    const d = sci.data!;
    let nan = 0, sum = 0;
    for (const v of d) Number.isNaN(v) ? nan++ : (sum += v);
    expect(nan).toBe(expected.nanCount);
    expect(sum).toBeCloseTo(expected.finiteSum, 3);
    for (const [x, y, v] of expected.values) {
      const got = d[y * sci.width + x];
      if (v === null) expect(Number.isNaN(got)).toBe(true);
      else expect(got).toBeCloseTo(v, 6);
    }
    expect(sci.header.BUNIT).toBe(expected.bunit);
  });
  it("reads the uint8 MASK plane", () => {
    const m = findHdu(hdus, "MASK")!;
    expect(m.data!.reduce((a, v) => a + (v ? 1 : 0), 0)).toBe(expected.maskSum);
  });
  it("matches astropy WCS both ways", () => {
    const t = makeTan(headerWcs(findHdu(hdus, "SCI")!.header));
    expected.pix.forEach(([x, y]: number[], i: number) => {
      const [ra, dec] = pix2world(t, x, y);
      expect(separationArcsec(ra, dec, expected.world[i][0], expected.world[i][1])).toBeLessThan(0.001);
      const back = world2pix(t, ra, dec)!;
      expect(back[0]).toBeCloseTo(x, 6);
      expect(back[1]).toBeCloseTo(y, 6);
    });
    const [ra, dec, x, y] = expected.world2pix[0];
    const p = world2pix(t, ra, dec)!;
    expect(p[0]).toBeCloseTo(x, 5);
    expect(p[1]).toBeCloseTo(y, 5);
    expect(pixelScaleArcsec(t)).toBeCloseTo(6.15, 3);
    expect(northAngle(t)).toBeCloseTo(0, 3); // north up
  });
});

describe("FITS header parsing", () => {
  it("handles strings with quotes, logicals, numbers and D exponents", () => {
    const cards = [
      "SIMPLE  =                    T", "BITPIX  =                  -32", "NAXIS   =                    0",
      "OBJECT  = 'Barnard''s Star'    / name", "EXPTIME =               1.5D+2", "FLAG    =                    F", "END",
    ].map(card).join("");
    const bytes = new TextEncoder().encode(cards.padEnd(2880, " "));
    const { header, dataOffset } = parseHeader(bytes.buffer as ArrayBuffer, 0);
    expect(header.OBJECT).toBe("Barnard's Star");
    expect(header.EXPTIME).toBe(150);
    expect(header.SIMPLE).toBe(true);
    expect(header.FLAG).toBe(false);
    expect(dataOffset).toBe(2880);
  });
});

describe("TAN with rotation (CD matrix)", () => {
  it("round-trips and finds north", () => {
    const a = (30 * Math.PI) / 180, s = 6.15 / 3600;
    const t = makeTan({ CTYPE1: "RA---TAN", CTYPE2: "DEC--TAN", CRPIX1: 50, CRPIX2: 50, CRVAL1: 133.76, CRVAL2: -7.24,
      CD1_1: -s * Math.cos(a), CD1_2: s * Math.sin(a), CD2_1: s * Math.sin(a), CD2_2: s * Math.cos(a) });
    const [ra, dec] = pix2world(t, 10, 80);
    const p = world2pix(t, ra, dec)!;
    expect(p[0]).toBeCloseTo(10, 6);
    expect(p[1]).toBeCloseTo(80, 6);
    expect(Math.abs(northAngle(t))).toBeCloseTo(a, 2);
  });
  it("rejects non-TAN projections", () => {
    expect(() => makeTan({ CTYPE1: "RA---SIN", CTYPE2: "DEC--SIN", CRPIX1: 1, CRPIX2: 1, CRVAL1: 0, CRVAL2: 0 })).toThrow();
  });
});
