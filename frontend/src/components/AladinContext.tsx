/**
 * Wide-field context with Aladin Lite (HiPS all-sky surveys): where the SPHEREx
 * cutout sits on the sky. The footprint box is the processed grid; the amber
 * marker is the target's predicted position on the current frame's date.
 * Aladin is loaded lazily so the main viewer never waits for it.
 */
import { useEffect, useRef, useState } from "react";
import type { Grid } from "../types";
import { makeTan, pix2world } from "../lib/wcs";
import { Panel } from "./ui";

const SURVEYS = [
  { id: "spherex", label: "SPHEREx DR1 (IRSA HiPS)", url: "https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube" },
  { id: "P/allWISE/color", label: "AllWISE colour" },
  { id: "P/2MASS/color", label: "2MASS colour" },
  { id: "P/DSS2/color", label: "DSS2 colour (optical)" },
];

// Minimal typing of the parts of Aladin Lite v3 we use.
interface AladinApi {
  setImageLayer(layer: unknown): void;
  setImageSurvey(id: string): void;
  gotoRaDec(ra: number, dec: number): void;
  setFoV(deg: number): void;
  addOverlay(o: unknown): void;
}
interface Overlay {
  add(shape: unknown): void;
  removeAll(): void;
}
type ALib = {
  init: Promise<void>;
  aladin(el: HTMLElement, opts: Record<string, unknown>): AladinApi;
  imageHiPS(url: string, opts?: Record<string, unknown>): unknown;
  graphicOverlay(opts: Record<string, unknown>): Overlay;
  polygon(pts: [number, number][], opts?: Record<string, unknown>): unknown;
  circle(ra: number, dec: number, r: number, opts?: Record<string, unknown>): unknown;
};

export function AladinContext(props: { grid: Grid | null; target?: [number, number] | null }) {
  return (
    <Panel title="Sky context (Aladin Lite)" defaultOpen={false}>
      <AladinView {...props} />
    </Panel>
  );
}

function AladinView({ grid, target }: { grid: Grid | null; target?: [number, number] | null }) {
  const el = useRef<HTMLDivElement>(null);
  const al = useRef<{ A: ALib; a: AladinApi; ov: Overlay } | null>(null);
  const [survey, setSurvey] = useState(SURVEYS[0].id);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!grid || al.current || !el.current) return;
    let dead = false;
    (async () => {
      try {
        const mod = await import("aladin-lite");
        const A = (mod.default ?? mod) as unknown as ALib;
        await A.init;
        if (dead || !el.current) return;
        const a = A.aladin(el.current, {
          fov: Math.max(0.15, grid.fov_deg * 4), target: `${grid.center_ra} ${grid.center_dec}`, cooFrame: "ICRSd",
          showReticle: false, showCooGrid: false, showLayersControl: false, showFullscreenControl: true, showProjectionControl: false,
          showSettingsControl: false, showShareControl: false, showSimbadPointerControl: true, showFrame: false, showContextMenu: true,
          showCooLocation: true, showZoomControl: true,
        });
        const ov = A.graphicOverlay({ color: "#fbbf24", lineWidth: 2 });
        a.addOverlay(ov);
        al.current = { A, a, ov };
        setReady(true);
      } catch (e) {
        setError((e as Error).message || "Aladin Lite failed to load");
      }
    })();
    return () => {
      dead = true;
    };
  }, [grid]);

  // Survey.
  useEffect(() => {
    const c = al.current;
    if (!c || !ready) return;
    const s = SURVEYS.find((x) => x.id === survey)!;
    try {
      if (s.url) c.a.setImageLayer(c.A.imageHiPS(s.url, { name: s.label, imgFormat: "png" }));
      else c.a.setImageSurvey(s.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [survey, ready]);

  // Footprint + target.
  useEffect(() => {
    const c = al.current;
    if (!c || !ready || !grid) return;
    c.a.gotoRaDec(grid.center_ra, grid.center_dec);
    c.ov.removeAll();
    try {
      const t = makeTan(grid.wcs);
      const [h, w] = grid.shape;
      const corners: [number, number][] = [[-0.5, -0.5], [w - 0.5, -0.5], [w - 0.5, h - 0.5], [-0.5, h - 0.5]].map(([x, y]) => pix2world(t, x, y));
      c.ov.add(c.A.polygon(corners, { color: "#fbbf24", lineWidth: 2 }));
      if (target) c.ov.add(c.A.circle(target[0], target[1], 15 / 3600, { color: "#fbbf24", lineWidth: 2 }));
    } catch {
      /* ignore */
    }
  }, [grid, target, ready]);

  return (
    <div className="space-y-2 text-xs">
      <select aria-label="Context survey" className="w-full rounded border border-slate-700 bg-slate-800 px-1 py-1 text-xs" value={survey} onChange={(e) => setSurvey(e.target.value)}>
        {SURVEYS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
      </select>
      <div ref={el} className="h-56 w-full overflow-hidden rounded bg-black" />
      {error && <p className="text-red-300">{error}</p>}
      <p className="text-[10px] text-slate-500">Box: processed field. Circle: target on this frame's date. HiPS tiles load directly from IRSA / CDS.</p>
      {grid && (
        <p className="flex gap-3 text-[11px]">
          <a className="text-sky-300 hover:underline" target="_blank" rel="noreferrer"
            href={`https://aladin.cds.unistra.fr/AladinLite/?target=${encodeURIComponent(`${grid.center_ra.toFixed(5)} ${grid.center_dec.toFixed(5)}`)}&fov=${Math.max(grid.fov_deg * 3, 0.05).toFixed(3)}`}>Open in Aladin Lite ↗</a>
          <a className="text-sky-300 hover:underline" target="_blank" rel="noreferrer"
            href={`https://simbad.cds.unistra.fr/simbad/sim-coo?Coord=${encodeURIComponent(`${grid.center_ra.toFixed(5)} ${grid.center_dec >= 0 ? "+" : ""}${grid.center_dec.toFixed(5)}`)}&Radius=${(grid.fov_deg * 60 / 2).toFixed(2)}&Radius.unit=arcmin`}>SIMBAD objects here ↗</a>
        </p>
      )}
    </div>
  );
}
