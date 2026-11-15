import { describe, expect, it } from "vitest";
import { FLAG, classOf, computeSectors, fmtDist, sectorOf, sourceOf, type StarBuffers } from "./galaxyData";

function stars(rows: { xyz: [number, number, number]; cls: number; src: number; flags?: number }[]): StarBuffers {
  const n = rows.length;
  const xyz = new Float32Array(n * 3), attrs = new Uint8Array(n * 4), conf = new Uint8Array(n);
  rows.forEach((r, i) => {
    xyz.set(r.xyz, i * 3);
    attrs[i * 4] = r.cls + 16 * r.src;
    attrs[i * 4 + 1] = r.flags ?? 0;
    conf[i] = 200;
  });
  return { n, xyz, attrs, conf };
}

describe("galaxy data helpers", () => {
  it("unpacks class and source from the attribute byte", () => {
    const s = stars([{ xyz: [1, 0, 0], cls: 6, src: 3 }]);
    expect(classOf(s.attrs, 0)).toBe(6);
    expect(sourceOf(s.attrs, 0)).toBe("spherex");
  });

  it("formats distances across scales", () => {
    expect(fmtDist(1 / 206264.806)).toBe("1.00 AU");
    expect(fmtDist(1.3)).toMatch(/^1\.30 pc · 4\.2 ly$/);
    expect(fmtDist(8178)).toMatch(/^8\.18 kpc/);
  });

  it("assigns sectors by shell and direction, and ignores > 100 pc", () => {
    expect(sectorOf(5, 0.1, 0.1)).toBe(sectorOf(6, 0.2, 0.3));
    expect(sectorOf(5, 0, 0)).not.toBe(sectorOf(-5, 0, 0));
    expect(sectorOf(5, 0, 0)).not.toBe(sectorOf(15, 0, 0));
    expect(sectorOf(150, 0, 0)).toBe(-1);
  });

  it("counts uncharted objects per sector", () => {
    const s = stars([
      { xyz: [5, 0.1, 0.1], cls: 10, src: 0 },
      { xyz: [5, 0.2, 0.1], cls: 5, src: 1, flags: FLAG.low_confidence },
      { xyz: [5, 0.3, 0.1], cls: 5, src: 2 },
      { xyz: [5, 0.4, 0.1], cls: 6, src: 3 },
    ]);
    const { sectors, sectorIndex } = computeSectors(s);
    const sec = sectors[sectorIndex[0]];
    expect(sec.total).toBe(4);
    expect(sec.unclassified).toBe(1);
    expect(sec.lowConf).toBe(1);
    expect(sec.catalogue).toBe(1);
    expect(sec.spherex).toBe(1);
    expect(sec.frontier).toBeCloseTo((1 + 1 + 0.5) / 4);
  });
});

describe("galactic to equatorial", () => {
  it("maps the Galactic centre and north pole to their ICRS positions", async () => {
    const { galToRaDec } = await import("./galaxyData");
    const [ra, dec] = galToRaDec(1, 0, 0);
    expect(ra).toBeCloseTo(266.405, 2);
    expect(dec).toBeCloseTo(-28.936, 2);
    const [rp, dp] = galToRaDec(0, 0, 5);
    expect(rp).toBeCloseTo(192.859, 2);
    expect(dp).toBeCloseTo(27.128, 2);
  });
});
