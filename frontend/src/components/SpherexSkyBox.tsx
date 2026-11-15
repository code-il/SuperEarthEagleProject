/**
 * Floating SPHEREx sky box on the Atlas: the SPHEREx DR1 all-sky image (IRSA HiPS cube, 102
 * wavelengths) centred on the selected star, or on the star nearest the point you are zoomed in on.
 * "Details" opens the full time-lapse Explorer on that field.
 *
 * The HiPS is built to order 5 (12.9″ pixels): zoom is capped so it is never magnified into blocks,
 * and the tiles (already asinh-stretched at IRSA) are shown in inferno colours with a linear 0–150 of
 * 255 cut, which shows faint stars without letting bright ones wash out the field.
 */
import { useEffect, useRef, useState } from "react";

const SPHEREX_HIPS = "https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube";
const FOV_MIN = 0.12, FOV_MAX = 3, FOV_START = 0.35;
const CUT_MAX = 150; // of 255 (PNG tile values)
export const CH_UM = "0.753 0.771 0.790 0.809 0.828 0.848 0.868 0.889 0.910 0.932 0.955 0.978 1.001 1.025 1.050 1.075 1.102 1.113 1.139 1.167 1.195 1.224 1.254 1.284 1.315 1.346 1.378 1.412 1.446 1.481 1.517 1.554 1.592 1.631 1.656 1.695 1.735 1.775 1.818 1.861 1.905 1.949 1.995 2.041 2.089 2.139 2.189 2.240 2.292 2.345 2.399 2.445 2.506 2.575 2.646 2.719 2.794 2.871 2.951 3.033 3.118 3.204 3.293 3.383 3.476 3.570 3.667 3.768 3.833 3.867 3.901 3.935 3.970 4.005 4.040 4.076 4.112 4.148 4.184 4.220 4.257 4.294 4.331 4.368 4.405 4.435 4.467 4.500 4.533 4.566 4.600 4.635 4.669 4.704 4.739 4.773 4.808 4.843 4.879 4.915 4.951 4.988".split(" ").map(Number);

interface AladinLite { gotoRaDec(r: number, d: number): void; setFoV(f: number): void; setFoVRange?(a: number, b: number): void; getFov?(): [number, number] }
interface Layer { setSliceNumber?(n: number): void; setColormap?(c: string, o?: object): void; setCuts?(a: number, b: number): void }

export interface SkyTarget { ra: number; dec: number; name: string; /** suggested HiPS slice (1-101, not 50/51) */ slice: number; nearest?: boolean }

export function SpherexSkyBox({ target, onDetails, className = "" }: { target: SkyTarget; onDetails: () => void; className?: string }) {
  const el = useRef<HTMLDivElement>(null);
  const al = useRef<{ a: AladinLite; layer: Layer } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [slice, setSlice] = useState(target.slice);
  const [open, setOpen] = useState(true);
  const sliceRef = useRef(slice);
  sliceRef.current = slice;

  useEffect(() => { setSlice(target.slice); }, [target.slice, target.name]);

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const mod = await import("aladin-lite");
        const A = (mod.default ?? mod) as { init: Promise<void>; aladin(e: HTMLElement, o: object): AladinLite; imageHiPS(u: string, o?: object): Layer };
        await A.init;
        if (dead || !el.current) return;
        const layer = A.imageHiPS(SPHEREX_HIPS, { name: "SPHEREx DR1", imgFormat: "png", colormap: "inferno", stretch: "linear", minCut: 0, maxCut: CUT_MAX });
        const a = A.aladin(el.current, { survey: layer, fov: FOV_START, target: `${target.ra} ${target.dec}`, cooFrame: "ICRSd", log: false,
          showReticle: true, reticleColor: "#5eead4", reticleSize: 18, showCooGrid: false, showLayersControl: false, showFullscreenControl: false,
          showProjectionControl: false, showSettingsControl: false, showShareControl: false, showFrame: false, showZoomControl: false,
          showCooLocation: false, showSimbadPointerControl: false, showContextMenu: false, showFov: false, showStatusBar: false, backgroundColor: "rgb(2,4,12)" });
        a.setFoVRange?.(FOV_MIN, FOV_MAX);
        a.setFoV(FOV_START);
        al.current = { a, layer };
        if (import.meta.env.DEV) (window as unknown as { __sky: unknown }).__sky = al.current;
        // The cube opens on slice 50, which (with 51) has no tiles at IRSA: set the slice and colours once attached.
        for (const ms of [150, 600, 1500, 3000]) setTimeout(() => {
          try { layer.setSliceNumber?.(sliceRef.current); layer.setColormap?.("inferno", { stretch: "linear" }); layer.setCuts?.(0, CUT_MAX); } catch { /* not ready */ }
        }, ms);
      } catch (e) { setErr((e as Error).message); }
    })();
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { const x = al.current; if (x) { x.a.gotoRaDec(target.ra, target.dec); x.a.setFoV(FOV_START); } }, [target.ra, target.dec]);
  useEffect(() => { try { al.current?.layer.setSliceNumber?.(slice); } catch { /* not a cube */ } }, [slice]);

  const zoom = (f: number) => { const a = al.current?.a; const cur = a?.getFov?.()[0] ?? FOV_START; a?.setFoV(Math.min(FOV_MAX, Math.max(FOV_MIN, cur * f))); };

  return (
    <div data-tour="skybox" className={`w-[290px] overflow-hidden rounded-md border border-teal-500/30 bg-slate-950/90 shadow-xl shadow-black/60 backdrop-blur-md ${className}`}>
      <div className="flex items-center gap-2 border-b border-teal-500/20 px-2 py-1.5">
        <span className="font-mono text-[10px] font-semibold tracking-[0.2em] text-teal-300">SPHEREx</span>
        <span className="min-w-0 flex-1 truncate text-[11px] text-slate-200" title={target.name}>{target.nearest ? "near " : ""}{target.name}</span>
        <button onClick={onDetails} className="rounded border border-amber-400/60 bg-amber-500/10 px-2 py-0.5 text-[11px] font-semibold text-amber-100 hover:bg-amber-500/25"
          title="Open the full SPHEREx Explorer here: every image in time order, blink, difference, light curves">Details →</button>
        <button onClick={() => setOpen((o) => !o)} className="px-1 text-xs text-slate-400 hover:text-white" aria-label={open ? "Collapse" : "Expand"}>{open ? "▾" : "▸"}</button>
      </div>
      <div className={open ? "" : "hidden"}>
        <div className="relative">
          <div ref={el} className="h-44 w-full bg-[#02040c]" />
          <div className="absolute bottom-1 left-1 flex gap-0.5">
            <button onClick={() => zoom(0.7)} className="h-5 w-5 rounded bg-black/70 text-xs text-slate-200 hover:bg-black" aria-label="Zoom in">+</button>
            <button onClick={() => zoom(1.4)} className="h-5 w-5 rounded bg-black/70 text-xs text-slate-200 hover:bg-black" aria-label="Zoom out">−</button>
          </div>
          <span className="pointer-events-none absolute bottom-1 right-1 rounded bg-black/60 px-1 font-mono text-[9px] text-slate-400">DR1 all-sky · 12.9″/px</span>
        </div>
        <label className="flex items-center gap-2 px-2 py-1 text-[10px] text-slate-400">λ
          <input type="range" min={1} max={101} value={slice} className="flex-1 accent-sky-400" aria-label="SPHEREx wavelength channel"
            onChange={(e) => { const v = Number(e.target.value); setSlice(v === 50 || v === 51 ? (v > slice ? 52 : 49) : v); }} />
          <span className="w-12 text-right font-mono text-slate-300">{CH_UM[slice - 1]?.toFixed(2)} µm</span>
        </label>
        {err && <p className="px-2 pb-1 text-[10px] text-red-300">{err}</p>}
      </div>
    </div>
  );
}

/** A good first wavelength: most stars (M dwarfs included) are brightest near 1.7 µm; T/Y brown dwarfs
 * at 4.5 µm (methane hides them at shorter wavelengths); young stars' warm dust at 3.5 µm. */
export function sliceForClass(key: string | undefined): number {
  return key === "bd" ? 88 : key === "yso" ? 66 : 36;
}
