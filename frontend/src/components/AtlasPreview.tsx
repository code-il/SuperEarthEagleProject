/**
 * A live, compact 3D Atlas for the home page's game card: the same stars, renderer and data as
 * /map, slowly orbiting. Drag to turn it; click inside to also let the mouse wheel zoom (the wheel
 * scrolls the page until you do, and again once the pointer leaves). Click a star to see its
 * name and open it in the full Atlas.
 *
 * The catalogue is large, so nothing is downloaded until the card scrolls into view, and the
 * scene stops rendering while it is off-screen.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import * as THREE from "three";
import { fmtDist, loadGalaxy, type GalaxyData } from "../lib/galaxyData";
import { GalaxyScene, type Filters } from "../lib/galaxyScene";

const FILTERS: Filters = {
  classMask: new Array(11).fill(true), sourceMask: [true, true, true, true], requireFlags: 0,
  maxDist: 5000, minConf: 0, colorMode: 0, uncharted: false,
};

export default function AtlasPreview({ className = "" }: { className?: string }) {
  const wrap = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const engine = useRef<GalaxyScene | null>(null);
  const [near, setNear] = useState(false);
  const [data, setData] = useState<GalaxyData | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [engaged, setEngaged] = useState(false);
  const [picked, setPicked] = useState<{ i: number; name: string; detail: string } | null>(null);
  const down = useRef<[number, number] | null>(null);

  // Start downloading only when the card is about to be seen; pause rendering when it is not.
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) setNear(true);
      if (engine.current) engine.current.paused = !e.isIntersecting;
    }, { rootMargin: "300px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!near) return;
    let dead = false;
    loadGalaxy((f) => !dead && setProgress(f)).then((d) => !dead && setData(d)).catch((e) => !dead && setError(String(e.message ?? e)));
    return () => { dead = true; };
  }, [near]);

  useEffect(() => {
    if (!data || !host.current || engine.current) return;
    const e = new GalaxyScene(host.current, data.stars, data.meta.classes, data.objects, { ...FILTERS, classMask: [...FILTERS.classMask], sourceMask: [...FILTERS.sourceMask] });
    e.setSystems(data.systems);
    Object.assign(e.controls, { autoRotate: true, autoRotateSpeed: 0.35, enableZoom: false, enablePan: false });
    e.flyTo(new THREE.Vector3(), 55, 3200);
    engine.current = e;
    const ro = new ResizeObserver(() => e.resize());
    ro.observe(host.current);
    return () => { ro.disconnect(); e.dispose(); engine.current = null; };
  }, [data]);

  const setZoom = (on: boolean) => {
    setEngaged(on);
    const e = engine.current;
    if (!e) return;
    e.controls.enableZoom = on;
    e.controls.enablePan = on;
    e.controls.autoRotate = !on;
  };

  const onPointerDown = (ev: React.PointerEvent) => { down.current = [ev.clientX, ev.clientY]; if (!engaged) setZoom(true); };
  const onPointerUp = (ev: React.PointerEvent) => {
    const d = down.current;
    down.current = null;
    const e = engine.current;
    if (!d || !e || !data || Math.hypot(ev.clientX - d[0], ev.clientY - d[1]) > 4) return;   // a drag, not a click
    const r = host.current!.getBoundingClientRect();
    const hit = e.pick(ev.clientX - r.left, ev.clientY - r.top);
    if (!hit) { setPicked(null); e.select(-1); return; }
    e.select(hit.i);
    const named = data.objects.find((o) => o.i === hit.i);
    const xyz = data.stars.xyz;
    const dist = Math.hypot(xyz[hit.i * 3], xyz[hit.i * 3 + 1], xyz[hit.i * 3 + 2]);
    const cls = data.meta.classes[data.stars.attrs[hit.i * 4] & 15];
    setPicked({ i: hit.i, name: named?.n ?? cls?.label ?? "Star", detail: `${cls?.label ?? "star"} · ${fmtDist(dist)} from the Sun` });
  };

  return (
    <div ref={wrap} className={`relative overflow-hidden bg-black ${className}`} onPointerLeave={() => setZoom(false)}>
      <div ref={host} className="absolute inset-0 cursor-grab active:cursor-grabbing" onPointerDown={onPointerDown} onPointerUp={onPointerUp}
        aria-label="Interactive 3D map of the stars around the Sun" role="img" />

      {!data && !error && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-center">
          <span className="font-hand text-3xl text-white">Charting the neighbourhood…</span>
          <div className="h-1 w-48 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-neutral-100 transition-all" style={{ width: `${Math.round(progress * 100)}%` }} /></div>
          <span className="font-mono text-[11px] text-neutral-500">Gaia DR3 · SIMBAD · NASA Exoplanet Archive · SPHEREx</span>
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center text-sm text-neutral-400">
          <span>The 3D Atlas could not be loaded here.</span>
          <span className="font-mono text-[11px] text-neutral-600">{error}</span>
          <Link to="/map" className="btn-primary px-4 py-2 text-sm">Open the Atlas</Link>
        </div>
      )}

      {data && (
        <>
          <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-3 bg-gradient-to-b from-black/70 to-transparent p-4 sm:p-5">
            <div>
              <div className="font-mono text-xs text-neutral-400">cosmIO Atlas — live</div>
              <div className="mt-1 text-sm text-neutral-200">{data.stars.n.toLocaleString()} real objects at their measured distances</div>
            </div>
            <span className="rounded-full bg-black/50 px-3 py-1 font-mono text-[11px] text-neutral-400 backdrop-blur">
              {engaged ? "drag · scroll zoom · click a star" : "drag to turn · click to zoom"}
            </span>
          </div>
          <div className="absolute inset-x-0 bottom-0 flex flex-wrap items-end justify-between gap-3 bg-gradient-to-t from-black/80 to-transparent p-4 sm:p-5">
            {picked ? (
              <div className="min-w-0">
                <div className="truncate text-base font-medium text-white">{picked.name}</div>
                <div className="text-xs text-neutral-400">{picked.detail}</div>
              </div>
            ) : <div className="text-xs text-neutral-500">The Sun is at the centre; each ring is a distance from it, in parsecs.</div>}
            <Link to={picked ? `/map?o=${picked.i}` : "/map"} className="btn-primary shrink-0 px-4 py-2 text-sm">
              {picked ? "Open in the Atlas →" : "Open the full Atlas →"}
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
