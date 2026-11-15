/**
 * SPHEREx Atlas: a zoomable 3D map of the real stellar neighbourhood (Gaia DR3 distances),
 * every object classified (SPHEREx spectrum > SIMBAD type > Gaia k-NN prediction), with
 * filters, an "uncharted" survey mode, planetary systems, and the 2D SPHEREx image viewer (Explore)
 * as a pop-up over the map (``?view=explore&ra=…&dec=…``). This is the app's front page.
 */
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import * as THREE from "three";
import {
  FLAG, SOURCES, SOURCE_LABEL, computeSectors, fetchObject, fetchSurvey, fmtDist, loadGalaxy, measureSpectrum,
  type GalaxyData, type NamedObject, type ObjectRecord, type Sector, type SpherexSpectrum, absMagOf, bpRpOf, classOf, galToRaDec, sourceOf,
} from "../lib/galaxyData";
import { GalaxyScene, type Filters } from "../lib/galaxyScene";
import { EXPLORE_KEYS } from "../lib/urlState";
import { AtlasTour, type TourStep } from "../components/AtlasTour";
import { SpherexSkyBox, sliceForClass, type SkyTarget } from "../components/SpherexSkyBox";
import { jdNow, orbitFor, phaseNote } from "../lib/orbits";

const ExplorePage = lazy(() => import("./ExplorePage"));

const SRC_SWATCH: Record<string, string> = { spherex: "#38bdf8", catalogue: "#4ade80", model: "#f5b942", none: "#ff4fd8" };
const FLAG_FILTERS: { bit: number; label: string }[] = [
  { bit: FLAG.planet_host, label: "Planet hosts" },
  { bit: FLAG.spherex, label: "SPHEREx spectrum" },
  { bit: FLAG.variable, label: "Variable stars" },
  { bit: FLAG.multiple, label: "Binaries / multiples" },
  { bit: FLAG.high_velocity, label: "High velocity (halo)" },
  { bit: FLAG.simbad_only, label: "Invisible to Gaia" },
];

function zoneName(d: number): string {
  if (d < 2e-4) return "PLANETARY SYSTEM";
  if (d < 0.5) return "STELLAR VICINITY";
  if (d < 15) return "SOLAR NEIGHBOURHOOD";
  if (d < 150) return "LOCAL BUBBLE";
  if (d < 1500) return "ORION SPUR";
  if (d < 8000) return "GALACTIC ARMS";
  return "MILKY WAY";
}

export default function MapPage() {
  const host = useRef<HTMLDivElement>(null);
  const engine = useRef<GalaxyScene | null>(null);
  const [data, setData] = useState<GalaxyData | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [hud, setHud] = useState<{ camDist: number; focus: number; orbitJd: number | null }>({ camDist: 12, focus: 0, orbitJd: null });
  const [sel, setSel] = useState<number>(-1);
  const [zoomStar, setZoomStar] = useState<number>(-1);
  const wide = useWide();
  const [params, setParams] = useSearchParams();
  const [panel, setPanel] = useState<"filters" | "uncharted" | "about">("filters");
  const [filters, setFilters] = useState<Filters>({
    classMask: new Array(11).fill(true), sourceMask: [true, true, true, true], requireFlags: 0,
    maxDist: 5000, minConf: 0, colorMode: 0, uncharted: false,
  });

  useEffect(() => {
    let dead = false;
    loadGalaxy((f) => !dead && setProgress(f)).then((d) => !dead && setData(d)).catch((e) => setError(String(e.message ?? e)));
    return () => { dead = true; };
  }, []);

  const sectors = useMemo(() => (data ? computeSectors(data.stars) : null), [data]);
  const nameIndex = useMemo(() => {
    const m = new Map<number, NamedObject>();
    data?.objects.forEach((o) => m.set(o.i, o));
    return m;
  }, [data]);

  const select = useCallback((i: number, fly = true) => {
    setSel(i);
    const e = engine.current;
    if (!e) return;
    if (i < 0) e.select(-1);
    else if (fly) e.flyToObject(i);
    else e.select(i);
    setParams((p) => { const n = new URLSearchParams(p); if (i >= 0) n.set("o", String(i)); else n.delete("o"); return n; }, { replace: true });
  }, [setParams]);

  // Engine lifecycle.
  useEffect(() => {
    if (!data || !host.current || engine.current) return;
    const e = new GalaxyScene(host.current, data.stars, data.meta.classes, data.objects, filters);
    e.setSystems(data.systems);
    engine.current = e;
    if (import.meta.env.DEV) (window as unknown as { __atlas: GalaxyScene }).__atlas = e;
    let last = 0, lastNear = 0;
    e.onFrame = ({ camDist, focusDistFromSun, orbitJd }) => {
      const now = performance.now();
      if (now - last > 200) { last = now; setHud({ camDist, focus: focusDistFromSun, orbitJd }); }
      // Zoomed in close: the sky box follows the star nearest the point you are looking at.
      if (now - lastNear > 500) {
        lastNear = now;
        setZoomStar(camDist < 3 ? e.nearestTo(e.focusGal(), Math.max(0.05, camDist * 0.6)) : -1);
      }
    };
    e.onLabelClick = (i) => select(i);
    const o = Number(params.get("o"));
    if (params.get("o") && Number.isFinite(o) && o >= 0 && o < data.stars.n) setTimeout(() => select(o), 300);
    else e.flyTo(new THREE.Vector3(), 14, 2500);
    // Mark objects measured live since the map was built.
    fetchSurvey().then((rows) => { for (const r of rows) if (r.i < data.stars.n) data.stars.attrs[r.i * 4 + 1] |= FLAG.spherex; }).catch(() => {});
    return () => { e.dispose(); engine.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  useEffect(() => { if (engine.current) { engine.current.filters = filters; engine.current.applyFilters(); } }, [filters]);

  // Explore pop-up (the 2D SPHEREx time-series viewer), driven by the URL so it can be shared.
  const exploreOpen = params.get("view") === "explore";
  const openExplore = useCallback((q?: Record<string, string>) => {
    setParams((p) => {
      const n = new URLSearchParams(p);
      for (const k of EXPLORE_KEYS) n.delete(k);
      n.set("view", "explore");
      for (const [k, v] of Object.entries(q ?? {})) n.set(k, v);
      return n;
    });
  }, [setParams]);
  const closeExplore = useCallback(() => {
    setParams((p) => {
      const n = new URLSearchParams(p);
      for (const k of [...EXPLORE_KEYS, "view"]) n.delete(k);
      return n;
    });
  }, [setParams]);
  useEffect(() => { if (engine.current) engine.current.paused = exploreOpen; }, [exploreOpen, data]);

  // SPHEREx sky box: the selected star, else the star you are zoomed in on.
  const boxIndex = sel >= 0 ? sel : zoomStar;
  const skyTarget = useMemo<SkyTarget | null>(() => {
    if (!data || boxIndex < 0) return null;
    const st = data.stars;
    const [ra, dec] = galToRaDec(st.xyz[boxIndex * 3], st.xyz[boxIndex * 3 + 1], st.xyz[boxIndex * 3 + 2]);
    const cls = data.meta.classes[classOf(st.attrs, boxIndex)];
    return { ra, dec, name: nameIndex.get(boxIndex)?.n ?? `${cls?.label ?? "star"} at ${fmtDist(Math.hypot(st.xyz[boxIndex * 3], st.xyz[boxIndex * 3 + 1], st.xyz[boxIndex * 3 + 2]))}`,
      slice: sliceForClass(cls?.key), nearest: sel < 0 };
  }, [data, boxIndex, sel, nameIndex]);
  const exploreAt = useCallback((i: number) => {
    if (!data || i < 0) return;
    const st = data.stars;
    const [ra, dec] = galToRaDec(st.xyz[i * 3], st.xyz[i * 3 + 1], st.xyz[i * 3 + 2]);
    const key = data.meta.classes[classOf(st.attrs, i)]?.key;
    const red = key === "bd" || (bpRpOf(st.attrs, i) ?? 0) > 3;
    openExplore({ ra: ra.toFixed(5), dec: dec.toFixed(5), fov: "0.05", det: red ? "6" : "3" });
  }, [data, openExplore]);
  const skyBox = skyTarget && <SpherexSkyBox target={skyTarget} onDetails={() => exploreAt(boxIndex)} />;
  const boxInCard = sel >= 0 && !wide;

  // Guided tour: opens by itself on the first visit, and from the Tour button.
  const [tour, setTour] = useState<number | null>(null);
  useEffect(() => {
    if (!data) return;
    let seen = true;
    try { seen = localStorage.getItem("atlas-tour-seen") === "1"; } catch { /* storage blocked */ }
    if (!seen && !params.get("o") && !exploreOpen) setTimeout(() => setTour(0), 2800);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);
  const endTour = useCallback(() => {
    setTour(null);
    try { localStorage.setItem("atlas-tour-seen", "1"); } catch { /* storage blocked */ }
  }, []);
  const tourSteps = useMemo<TourStep[]>(() => {
    if (!data) return [];
    const e = () => engine.current;
    const byName = (n: string) => data.objects.find((o) => o.n.toLowerCase() === n.toLowerCase())?.i ?? -1;
    const flyOut = (d: number) => e()?.flyTo(new THREE.Vector3(), d, 2600);
    const reset = (uncharted = false) => { setPanel(uncharted ? "uncharted" : "filters"); setFilters((f) => ({ ...f, uncharted })); };
    const pct = Math.round((data.meta.model?.accuracy ?? 0.9) * 100);
    const n = data.stars.n.toLocaleString();
    return [
      { title: "Welcome to the SPHEREx Atlas", run: () => { select(-1, false); reset(); e()?.goHome(); },
        body: <><p>Every point is a real star, brown dwarf or white dwarf at its measured distance: {n} objects placed with Gaia DR3 parallaxes, typed with SIMBAD and the NASA Exoplanet Archive, and some classified from their SPHEREx spectra.</p>
          <p>You start at the Sun. The rings are distances in parsecs (1 pc = 3.26 light-years).</p></> },
      { title: "Moving around", target: "controls",
        body: <p>Drag to rotate, right-drag to pan, and scroll to zoom toward the cursor. Click a star to select it; double-click to fly to it. Press <b>H</b> to come back to the Sun.</p> },
      { title: "What size and brightness mean", target: "scale", run: () => flyOut(40),
        body: <><p>Stars are drawn as they would look from where the camera is: each one's brightness comes from its real luminosity and its distance from you, so stars grow as you approach.</p>
          <p>The Sun is the exception: it is marked with a fixed glow (and a “Sun” label) so you can always find home. Zoom right in on it and you see its true size, 1 solar radius.</p></> },
      { title: "Zooming out", target: "scale", run: () => flyOut(2500),
        body: <p>The map has every star with a good parallax out to 100 pc. Beyond that it shows only bright stars (one in three Gaia stars brighter than G = 10.5, out to about 4 kpc), so the neighbourhood looks denser than the arms. The panel at the bottom left always shows the current scale.</p> },
      { title: "The Milky Way", run: () => flyOut(30000),
        body: <p>At this scale the spiral disk is an <b>illustration</b> for orientation, not data: parallaxes are too uncertain to place single stars this far away. The Sun is 8.2 kpc from the Galactic centre.</p> },
      { title: "Object classes", target: "classes", run: () => { reset(); e()?.goHome(); },
        body: <p>Each colour is a class: hot O/B stars through cool M dwarfs, then brown dwarfs, white dwarfs, giants and young stars. Untick a class to hide it, or click its name to show only that class. The counts are for the whole map.</p> },
      { title: "How each object was classified", target: "sources",
        body: <><p><span className="text-emerald-300">Catalogue</span>: a published spectral or object type (SIMBAD). <span className="text-amber-300">Model</span>: predicted from Gaia colour and luminosity by comparing it with the 15 most similar catalogued stars ({pct}% right on stars it was not trained on).</p>
          <p><span className="text-sky-300">SPHEREx</span>: classified from its own infrared spectrum. <span className="text-fuchsia-300">Unclassified</span>: nobody has a type for it yet. Use the confidence slider to hide uncertain predictions.</p></> },
      { title: "Find anything", target: "search",
        body: <p>Search by name or alias (Sirius, Polaris, Proxima Centauri, TRAPPIST-1), or click the box for a list of famous neighbours.</p> },
      { title: "Inspect a star", target: "card", run: () => { const i = byName("TRAPPIST-1"); if (i >= 0) select(i, true); },
        body: <><p>The card shows the class and the evidence for it and Gaia's measurements. The SPHEREx box shows the sky around the star in SPHEREx's all-sky map; it also follows the star you are zoomed in on.</p>
          <p>TRAPPIST-1, 12.5 pc away, has seven known planets. When you fly to a planet host, its orbits are drawn to scale from the NASA Exoplanet Archive, with the estimated habitable zone in green. Planets move on Kepler orbits at one fixed rate, 1 day per second (Mercury would take 88 s), starting from today's predicted positions; the yellow arc is the ±1σ uncertainty of each position.</p></> },
      { title: "Reading a SPHEREx spectrum", target: "spectrum", run: () => { const i = byName("eps Ind B"); if (i >= 0) select(i, true); },
        body: <><p>SPHEREx sees 102 infrared colours, from 0.75 to 5 µm. This is ε Indi B, a pair of brown dwarfs 3.7 pc away. The dashed line is the best-fitting blackbody (about 1,900 K), and the dip in the shaded H₂O band is water vapour in their atmospheres.</p>
          <p>For any other object, press <b>Measure</b> to build its spectrum from every SPHEREx image of it. This takes 1–3 minutes the first time.</p></> },
      { title: "The images behind it", target: "skybox",
        body: <p><b>Details</b> in the SPHEREx box (or <b>Open SPHEREx images</b> on the card) opens the Explorer over the map, showing every SPHEREx frame of that patch of sky in time order. Play it to watch nearby stars creep across the field, blink or subtract two dates, and plot light curves and spectra. Press Esc to come back.</p> },
      { title: "The frontier", target: "panel", run: () => { select(-1, false); reset(true); },
        body: <p>Frontier mode dims everything that is known and shows in pink what is unclassified or uncertain. Sectors are ranked by how much is still unknown. Pick one, fly there, and measure its objects' spectra to chart them.</p> },
      { title: "Your turn", run: () => { reset(); e()?.goHome(); },
        body: <p>Press <b>Tour</b> in the top bar to see this again. Keys: <b>H</b> go home, <b>F</b> focus on the selected star, <b>U</b> frontier mode, <b>Esc</b> deselect.</p> },
    ];
  }, [data, select]);

  // Click to pick (ignore drags).
  const down = useRef<[number, number] | null>(null);
  const onPointerDown = (ev: React.PointerEvent) => { down.current = [ev.clientX, ev.clientY]; };
  const onPointerUp = (ev: React.PointerEvent) => {
    const d = down.current;
    down.current = null;
    if (!d || Math.hypot(ev.clientX - d[0], ev.clientY - d[1]) > 5 || !engine.current || !host.current) return;
    const r = host.current.getBoundingClientRect();
    const hit = engine.current.pick(ev.clientX - r.left, ev.clientY - r.top);
    if (hit && ev.detail < 2) select(hit.i, false);
  };
  const onDoubleClick = (ev: React.MouseEvent) => {
    if (!engine.current || !host.current) return;
    const r = host.current.getBoundingClientRect();
    const hit = engine.current.pick(ev.clientX - r.left, ev.clientY - r.top);
    if (hit) select(hit.i, true);
  };

  useEffect(() => {
    const k = (ev: KeyboardEvent) => {
      if ((ev.target as HTMLElement)?.tagName === "INPUT") return;
      if (exploreOpen) { if (ev.key === "Escape") closeExplore(); return; }
      if (ev.key === "Escape") select(-1, false);
      if (ev.key === "h" || ev.key === "H") engine.current?.goHome();
      if ((ev.key === "f" || ev.key === "F") && sel >= 0) engine.current?.flyToObject(sel);
      if (ev.key === "u" || ev.key === "U") setFilters((f) => ({ ...f, uncharted: !f.uncharted }));
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [sel, select, exploreOpen, closeExplore]);

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-slate-300">
        <div className="max-w-lg space-y-3">
          <p className="text-lg font-semibold text-red-300">The map data could not be loaded.</p>
          <p className="font-mono text-xs text-slate-400">{error}</p>
          <Link to="/" className="text-amber-300 underline">Back home</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#01030a] text-slate-200 select-none">
      <div ref={host} className="absolute inset-0" onPointerDown={onPointerDown} onPointerUp={onPointerUp} onDoubleClick={onDoubleClick} />
      {!data && <Loader progress={progress} />}
      <Scanlines />
      <TopBar data={data} onSelect={(i) => select(i)} onHome={() => engine.current?.goHome()} onExplore={() => openExplore()} onTour={() => setTour(0)} />
      {data && (
        <>
          <aside data-tour="panel" className="absolute left-3 top-16 bottom-14 z-10 flex w-[300px] flex-col overflow-hidden rounded-md border border-teal-500/25 bg-slate-950/80 backdrop-blur-md max-md:hidden">
            <div className="flex border-b border-teal-500/20 text-[11px] font-semibold tracking-widest">
              {(["filters", "uncharted", "about"] as const).map((p) => (
                <button key={p} onClick={() => { setPanel(p); if (p === "uncharted") setFilters((f) => ({ ...f, uncharted: true })); }}
                  className={`flex-1 py-2 uppercase ${panel === p ? "bg-teal-500/15 text-teal-200" : "text-slate-400 hover:text-slate-200"}`}>
                  {p === "uncharted" ? "Frontier" : p}
                </button>
              ))}
            </div>
            <div className="flex-1 overflow-y-auto p-3 text-xs">
              {panel === "filters" && <FiltersPanel data={data} filters={filters} setFilters={setFilters} />}
              {panel === "uncharted" && sectors && (
                <FrontierPanel sectors={sectors.sectors} filters={filters} setFilters={setFilters}
                  onFly={(s) => engine.current?.flyTo(new THREE.Vector3(...s.centroid), Math.max(4, (s.shell[1] - s.shell[0]) * 1.2))}
                  data={data} sectorIndex={sectors.sectorIndex} onSelect={(i) => select(i)} />
              )}
              {panel === "about" && <AboutPanel data={data} />}
            </div>
          </aside>
          {sel >= 0 && <ObjectCard key={sel} i={sel} data={data} named={nameIndex.get(sel)} onClose={() => select(-1, false)} onFly={() => engine.current?.flyToObject(sel)} onExplore={openExplore}
            sky={boxInCard && !exploreOpen ? skyBox : null} />}
          {skyBox && !boxInCard && !exploreOpen && (
            <div className={`absolute bottom-14 z-10 max-md:hidden ${sel >= 0 ? "right-[396px]" : "right-3"}`}>{skyBox}</div>
          )}
          <Hud camDist={hud.camDist} focus={hud.focus} uncharted={filters.uncharted} />
          {hud.orbitJd != null && sel >= 0 && <OrbitLegend jd={hud.orbitJd} unplotted={engine.current?.unplottedPlanets ?? []} />}
        </>
      )}
      {tour !== null && !exploreOpen && <AtlasTour steps={tourSteps} step={tour} onStep={setTour} onClose={endTour} />}
      {exploreOpen && (
        <div className="absolute inset-0 z-40 bg-slate-950/70 p-2 backdrop-blur-sm md:p-4" role="dialog" aria-modal="true" aria-label="SPHEREx image explorer">
          <div className="h-full overflow-hidden rounded-lg border border-teal-500/30 shadow-2xl shadow-black">
            <Suspense fallback={<div className="flex h-full items-center justify-center bg-slate-950 text-sm text-slate-400">Loading the image viewer…</div>}>
              <ExplorePage embedded onClose={closeExplore} />
            </Suspense>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
function Loader({ progress }: { progress: number }) {
  return (
    <div className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-3 font-mono text-xs text-teal-200">
      <div className="text-sm tracking-[0.4em]">CHARTING THE NEIGHBOURHOOD</div>
      <div className="h-1 w-64 overflow-hidden rounded bg-slate-800"><div className="h-full bg-teal-400 transition-all" style={{ width: `${Math.round(progress * 100)}%` }} /></div>
      <div className="text-slate-500">Gaia DR3 · SIMBAD · NASA Exoplanet Archive · SPHEREx DR1+QR3</div>
    </div>
  );
}

function Scanlines() {
  return <div className="pointer-events-none absolute inset-0 z-[1] opacity-[0.025] [background:repeating-linear-gradient(0deg,#fff_0,#fff_1px,transparent_1px,transparent_4px)]" />;
}

function TopBar({ data, onSelect, onHome, onExplore, onTour }: { data: GalaxyData | null; onSelect: (i: number) => void; onHome: () => void; onExplore: () => void; onTour: () => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const results = useMemo(() => {
    if (!data || q.trim().length < 2) return [];
    const s = q.trim().toLowerCase();
    const out: { o: NamedObject; rank: number; via?: string }[] = [];
    for (const o of data.objects) {
      const n = o.n.toLowerCase();
      if (n.includes(s)) out.push({ o, rank: n === s ? 0 : n.startsWith(s) ? 1 : 3 });
      else {
        const a = o.a?.find((x) => x.toLowerCase().includes(s));
        if (a) out.push({ o, rank: a.toLowerCase().startsWith(s) ? 2 : 4, via: a });
      }
      if (out.length > 400) break;
    }
    return out.sort((a, b) => a.rank - b.rank || a.o.d - b.o.d).slice(0, 10);
  }, [q, data]);
  const quick = useMemo(() => {
    if (!data) return [];
    const want = ["Proxima Centauri", "Barnard's star", "Wolf 359", "TRAPPIST-1", "eps Ind B", "Luhman 16", "Sirius", "Betelgeuse"];
    return want.map((w) => data.objects.find((o) => o.n.toLowerCase() === w.toLowerCase()) ?? data.objects.find((o) => o.n.toLowerCase().startsWith(w.toLowerCase()))).filter(Boolean) as NamedObject[];
  }, [data]);
  return (
    <header className="absolute inset-x-3 top-3 z-20 flex items-center gap-3">
      <Link to="/" className="flex items-center gap-2 rounded-md border border-teal-500/25 bg-slate-950/80 px-3 py-2 backdrop-blur-md">
        <img src="/favicon.svg" alt="" className="h-5 w-5" />
        <span className="font-mono text-xs font-bold tracking-[0.25em]">SPHEREx <span className="text-teal-300">ATLAS</span></span>
      </Link>
      <div className="relative w-full max-w-md" data-tour="search">
        <input value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder={data ? `Search ${data.objects.length.toLocaleString()} named objects… (e.g. TRAPPIST-1)` : "Loading…"}
          className="w-full rounded-md border border-teal-500/25 bg-slate-950/80 px-3 py-2 font-mono text-xs backdrop-blur-md placeholder:text-slate-500 focus:border-teal-300 focus:outline-none" />
        {open && (results.length > 0 || (!q && quick.length > 0)) && (
          <ul className="absolute mt-1 w-full overflow-hidden rounded-md border border-teal-500/25 bg-slate-950/95 text-xs shadow-xl">
            {!q && <li className="px-3 py-1 text-[10px] uppercase tracking-widest text-slate-500">Jump to</li>}
            {(q ? results : quick.map((o) => ({ o, via: undefined as string | undefined }))).map(({ o, via }) => (
              <li key={o.i}>
                <button onMouseDown={() => { onSelect(o.i); setQ(""); setOpen(false); }} className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left hover:bg-teal-500/10">
                  <span className="truncate">{o.n}{via && <span className="text-slate-500"> · {via}</span>}</span>
                  <span className="shrink-0 font-mono text-[10px] text-slate-500">{data?.meta.classes[o.c]?.label} · {fmtDist(o.d)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <button onClick={onHome} className="rounded-md border border-teal-500/25 bg-slate-950/80 px-3 py-2 font-mono text-xs backdrop-blur-md hover:text-teal-200" title="Return to the Sun (H)">☉ Sun</button>
      <button onClick={onTour} disabled={!data} className="rounded-md border border-amber-400/50 bg-slate-950/80 px-3 py-2 font-mono text-xs text-amber-200 backdrop-blur-md hover:bg-amber-500/10 disabled:opacity-40" title="A guided tour of the Atlas">? Tour</button>
      <nav className="ml-auto flex gap-1 rounded-md border border-teal-500/25 bg-slate-950/80 px-2 py-1.5 text-xs backdrop-blur-md max-sm:hidden">
        <button onClick={onExplore} className="px-2 py-0.5 text-slate-300 hover:text-white">Explore images</button>
        <Link to="/targets" className="px-2 py-0.5 text-slate-300 hover:text-white">Challenge targets</Link>
        <Link to="/about" className="px-2 py-0.5 text-slate-300 hover:text-white">About</Link>
      </nav>
    </header>
  );
}

function Hud({ camDist, focus, uncharted }: { camDist: number; focus: number; uncharted: boolean }) {
  return (
    <div className="pointer-events-none absolute inset-x-3 bottom-3 z-10 flex items-end justify-between gap-3 font-mono text-[10px] text-teal-200/80">
      <div data-tour="scale" className="rounded-md border border-teal-500/20 bg-slate-950/70 px-3 py-1.5 backdrop-blur">
        <div className="tracking-[0.3em] text-teal-300">{zoneName(camDist)}</div>
        <div>VIEW SCALE {fmtDist(camDist)} · FOCUS {fmtDist(focus)} FROM SUN</div>
      </div>
      {uncharted && <div className="rounded-md border border-fuchsia-400/40 bg-fuchsia-950/50 px-3 py-1.5 tracking-widest text-fuchsia-200">FRONTIER MODE · pink = unclassified or uncertain · the pulsing is a highlight, not variability</div>}
      <div data-tour="controls" className="rounded-md border border-teal-500/20 bg-slate-950/70 px-3 py-1.5 text-right backdrop-blur max-sm:hidden">
        drag rotate · right-drag pan · scroll zoom to cursor · click select · dbl-click fly · <b>H</b> home · <b>F</b> focus · <b>U</b> frontier
      </div>
    </div>
  );
}

function Section({ title, children, right, tour }: { title: string; children: React.ReactNode; right?: React.ReactNode; tour?: string }) {
  return (
    <section className="mb-4" data-tour={tour}>
      <div className="mb-1.5 flex items-center justify-between text-[10px] font-semibold uppercase tracking-[0.2em] text-teal-300/90"><span>{title}</span>{right}</div>
      {children}
    </section>
  );
}

function FiltersPanel({ data, filters, setFilters }: { data: GalaxyData; filters: Filters; setFilters: React.Dispatch<React.SetStateAction<Filters>> }) {
  const { meta } = data;
  const setClass = (id: number, v: boolean) => setFilters((f) => { const m = [...f.classMask]; m[id] = v; return { ...f, classMask: m }; });
  const only = (id: number) => setFilters((f) => ({ ...f, classMask: f.classMask.map((_, k) => k === id) }));
  const logMax = Math.log10(filters.maxDist);
  return (
    <>
      <Section title="Classification" tour="classes" right={<button className="text-slate-400 hover:text-white" onClick={() => setFilters((f) => ({ ...f, classMask: f.classMask.map(() => true) }))}>all</button>}>
        <ul className="space-y-0.5">
          {meta.classes.map((c) => (
            <li key={c.id} className="group flex items-center gap-2">
              <input type="checkbox" checked={filters.classMask[c.id]} onChange={(e) => setClass(c.id, e.target.checked)} className="accent-teal-400" aria-label={c.label} />
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: c.color, boxShadow: `0 0 6px ${c.color}` }} />
              <button className="flex-1 truncate text-left hover:text-white" title={`${c.desc}. Click to show only this class.`} onClick={() => only(c.id)}>{c.label}</button>
              <span className="font-mono text-[10px] text-slate-500">{(meta.counts[c.key] ?? 0).toLocaleString()}</span>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="How it was classified" tour="sources">
        <ul className="space-y-0.5">
          {SOURCES.map((s, k) => (
            <li key={s} className="flex items-center gap-2">
              <input type="checkbox" checked={filters.sourceMask[k]} className="accent-teal-400" aria-label={SOURCE_LABEL[s]}
                onChange={(e) => setFilters((f) => { const m = [...f.sourceMask]; m[k] = e.target.checked; return { ...f, sourceMask: m }; })} />
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: SRC_SWATCH[s] }} />
              <span className="flex-1">{SOURCE_LABEL[s]}</span>
              <span className="font-mono text-[10px] text-slate-500">{(meta.class_sources[s] ?? 0).toLocaleString()}</span>
            </li>
          ))}
        </ul>
        <label className="mt-2 block text-slate-400">Min. model confidence: <b className="text-slate-200">{Math.round(filters.minConf * 100)}%</b>
          <input type="range" min={0} max={0.95} step={0.05} value={filters.minConf} onChange={(e) => setFilters((f) => ({ ...f, minConf: Number(e.target.value) }))} className="w-full accent-amber-400" />
        </label>
      </Section>
      <Section title="Show only">
        <div className="flex flex-wrap gap-1">
          {FLAG_FILTERS.map(({ bit, label }) => {
            const on = (filters.requireFlags & bit) !== 0;
            return <button key={bit} onClick={() => setFilters((f) => ({ ...f, requireFlags: f.requireFlags ^ bit }))}
              className={`rounded border px-2 py-0.5 ${on ? "border-teal-300 bg-teal-500/20 text-teal-100" : "border-slate-700 text-slate-400 hover:text-slate-200"}`}>{label}</button>;
          })}
        </div>
        <p className="mt-1 text-[10px] text-slate-500">Several selected = any of them.</p>
      </Section>
      <Section title="Distance from the Sun">
        <input type="range" min={0.5} max={3.7} step={0.01} value={logMax} onChange={(e) => setFilters((f) => ({ ...f, maxDist: Math.pow(10, Number(e.target.value)) }))} className="w-full accent-teal-400" aria-label="Maximum distance" />
        <div className="flex justify-between font-mono text-[10px] text-slate-400"><span>3 pc</span><b className="text-slate-200">≤ {fmtDist(filters.maxDist)}</b><span>5 kpc</span></div>
      </Section>
      <Section title="Colour stars by">
        <div className="grid grid-cols-3 gap-1">
          {(["Class", "True colour", "Source"] as const).map((l, k) => (
            <button key={l} onClick={() => setFilters((f) => ({ ...f, colorMode: k as 0 | 1 | 2 }))}
              className={`rounded border px-1 py-1 ${filters.colorMode === k ? "border-teal-300 bg-teal-500/20" : "border-slate-700 text-slate-400"}`}>{l}</button>
          ))}
        </div>
      </Section>
    </>
  );
}

function FrontierPanel({ sectors, filters, setFilters, onFly, data, sectorIndex, onSelect }: {
  sectors: Sector[]; filters: Filters; setFilters: React.Dispatch<React.SetStateAction<Filters>>; onFly: (s: Sector) => void;
  data: GalaxyData; sectorIndex: Int16Array; onSelect: (i: number) => void;
}) {
  const ranked = useMemo(() => sectors.filter((s) => s.total >= 20).sort((a, b) => b.frontier - a.frontier), [sectors]);
  const [open, setOpen] = useState<number | null>(null);
  const names = useMemo(() => new Map(data.objects.map((o) => [o.i, o.n])), [data]);
  const tot = sectors.reduce((a, s) => ({ t: a.t + s.total, u: a.u + s.unclassified + s.lowConf, x: a.x + s.spherex }), { t: 0, u: 0, x: 0 });
  const targets = useMemo(() => {
    if (open === null) return [];
    const out: { i: number; score: number }[] = [];
    for (let i = 0; i < data.stars.n; i++) {
      if (sectorIndex[i] !== open) continue;
      const src = sourceOf(data.stars.attrs, i), fl = data.stars.attrs[i * 4 + 1];
      if (src === "none" || (src === "model" && fl & FLAG.low_confidence)) out.push({ i, score: absMagOf(data.stars.attrs, i) });
    }
    return out.sort((a, b) => a.score - b.score).slice(0, 12);
  }, [open, data, sectorIndex]);
  return (
    <>
      <label className="mb-3 flex items-center gap-2 rounded border border-fuchsia-400/40 bg-fuchsia-950/30 p-2">
        <input type="checkbox" checked={filters.uncharted} onChange={(e) => setFilters((f) => ({ ...f, uncharted: e.target.checked }))} className="accent-fuchsia-400" />
        <span>Highlight the uncharted <span className="text-slate-400">(unclassified, or model confidence &lt; 60 %)</span></span>
      </label>
      <p className="mb-3 text-slate-400">
        Within 100 pc, <b className="text-fuchsia-200">{tot.u.toLocaleString()}</b> of {tot.t.toLocaleString()} objects have no reliable class, and only{" "}
        <b className="text-sky-300">{tot.x}</b> have a SPHEREx spectrum. Each sector below is a slice of space (distance shell × Galactic direction). Open one, pick an
        uncharted object and measure its SPHEREx spectrum to classify it.
      </p>
      <Section title="Most uncharted sectors">
        <ul className="space-y-1">
          {ranked.slice(0, 24).map((s) => (
            <li key={s.id} className="rounded border border-slate-800 bg-slate-900/50">
              <button className="w-full px-2 py-1.5 text-left hover:bg-slate-800/60" onClick={() => { setOpen(open === s.id ? null : s.id); onFly(s); }}>
                <div className="flex items-center justify-between"><b className="text-slate-100">{s.name}</b><span className="font-mono text-fuchsia-300">{Math.round(s.frontier * 100)}%</span></div>
                <div className="font-mono text-[10px] text-slate-500">{s.shell[0]}–{s.shell[1]} pc · l {s.l[0]}–{s.l[1]}° · b {s.b[0]}…{s.b[1]}° · {s.total.toLocaleString()} obj</div>
                <div className="mt-1 flex h-1 overflow-hidden rounded bg-slate-800">
                  <span style={{ width: `${(s.spherex / s.total) * 100}%`, background: SRC_SWATCH.spherex }} />
                  <span style={{ width: `${(s.catalogue / s.total) * 100}%`, background: SRC_SWATCH.catalogue }} />
                  <span style={{ width: `${((s.predicted - s.lowConf) / s.total) * 100}%`, background: SRC_SWATCH.model }} />
                  <span style={{ width: `${((s.unclassified + s.lowConf) / s.total) * 100}%`, background: SRC_SWATCH.none }} />
                </div>
              </button>
              {open === s.id && (
                <ul className="border-t border-slate-800 px-2 py-1">
                  {targets.length === 0 && <li className="text-slate-500">Nothing uncharted here.</li>}
                  {targets.map(({ i }) => (
                    <li key={i}><button onClick={() => onSelect(i)} className="flex w-full justify-between py-0.5 text-left hover:text-white">
                      <span className="truncate">{names.get(i) ?? `Uncharted #${i}`}</span>
                      <span className="font-mono text-[10px] text-slate-500">{sourceOf(data.stars.attrs, i) === "none" ? "no colour" : `${Math.round((data.stars.conf[i] / 255) * 100)}% ${data.meta.classes[classOf(data.stars.attrs, i)].label}`}</span>
                    </button></li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}

function AboutPanel({ data }: { data: GalaxyData }) {
  const m = data.meta;
  const prov = m.provenance as Record<string, Record<string, unknown>>;
  return (
    <div className="space-y-3 leading-relaxed text-slate-300">
      <p>Every point is a real object at its measured distance. Nothing is placed by hand except the faint Milky Way disk seen from far away, which is an <b>illustration</b> for orientation.</p>
      <Section title="Data">
        <ul className="list-disc space-y-1 pl-4">
          <li><b>Gaia DR3</b>: {Number(prov.gaia?.n ?? 0).toLocaleString()} stars. Every star ≤ 100 pc with parallax/error ≥ 10, plus 1 in 3 bright (G &lt; 10.5) stars to 4 kpc.</li>
          <li><b>SIMBAD</b>: names, object and spectral types; {Number(prov.simbad?.n_simbad_only ?? 0).toLocaleString()} objects Gaia cannot see (cold brown dwarfs).</li>
          <li><b>NASA Exoplanet Archive</b>: {Number(prov.exoplanets?.n_planets ?? 0).toLocaleString()} planets in {Number(prov.exoplanets?.n_systems ?? 0).toLocaleString()} systems.</li>
          <li><b>SPHEREx DR1 + QR3</b>: 0.75–5 µm spectra from forced photometry on every image of the object ({Number(prov.spherex?.n ?? 0)} so far, more measured on demand).</li>
        </ul>
      </Section>
      <Section title="Classification model">
        <p>{m.model.method}, trained on {m.model.n_train.toLocaleString()} {m.model.labels}.</p>
        <p className="mt-1">Held-out accuracy: <b className="text-amber-300">{Math.round(m.model.accuracy * 100)}%</b> on {m.model.n_test.toLocaleString()} stars
          {m.model.accuracy_within_one_type_main_sequence != null && <> ({Math.round(m.model.accuracy_within_one_type_main_sequence * 100)}% within one spectral type on the main sequence)</>}.</p>
        <table className="mt-2 w-full font-mono text-[10px]">
          <tbody>{Object.entries(m.model.per_class).map(([k, v]) => (
            <tr key={k}><td className="text-slate-400">{m.classes.find((c) => c.key === k)?.label}</td><td className="text-right">{Math.round(v.recall * 100)}% recall</td><td className="text-right text-slate-500">n={v.n_test}</td></tr>
          ))}</tbody>
        </table>
        <p className="mt-2 text-slate-400">A SPHEREx spectrum overrides the catalogue type and the prediction when its confidence is ≥ 50 %. Predictions are not discoveries.</p>
      </Section>
      <p className="font-mono text-[10px] text-slate-500">Built {m.built}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
function ObjectCard({ i, data, named, onClose, onFly, onExplore, sky }: {
  i: number; data: GalaxyData; named?: NamedObject; onClose: () => void; onFly: () => void; onExplore: (q: Record<string, string>) => void; sky?: React.ReactNode;
}) {
  const [rec, setRec] = useState<ObjectRecord | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [spx, setSpx] = useState<SpherexSpectrum | null>(null);
  const [measuring, setMeasuring] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const st = data.stars;
  const cls = data.meta.classes[classOf(st.attrs, i)];
  const src = sourceOf(st.attrs, i);
  const xyz: [number, number, number] = [st.xyz[i * 3], st.xyz[i * 3 + 1], st.xyz[i * 3 + 2]];
  const dist = Math.hypot(...xyz);
  const system = data.systems.find((s) => s.i === i) ?? null;

  useEffect(() => {
    fetchObject(i).then((r) => { setRec(r); if (r.spherex) setSpx(r.spherex); }).catch((e) => setErr(String(e.message ?? e)));
  }, [i]);
  useEffect(() => {
    if (!measuring) return;
    const t0 = Date.now();
    const id = setInterval(() => setElapsed((Date.now() - t0) / 1000), 500);
    return () => clearInterval(id);
  }, [measuring]);

  const measure = async () => {
    setMeasuring(true);
    setErr(null);
    try {
      const s = await measureSpectrum(i);
      setSpx(s);
      st.attrs[i * 4 + 1] |= FLAG.spherex;
    } catch (e) {
      setErr(`SPHEREx measurement failed: ${(e as Error).message}`);
    } finally {
      setMeasuring(false);
    }
  };

  const title = rec?.name ?? named?.n ?? (rec?.gaia_dr3_id ? `Gaia DR3 ${rec.gaia_dr3_id}` : `Object #${i}`);
  const ra = rec?.ra, dec = rec?.dec;
  // Cool objects are brightest at the red end (detector 6, 3.8–5 µm); others at detector 3 (1.6–2.4 µm).
  const exploreQuery = ra != null && dec != null
    ? { ra: ra.toFixed(5), dec: dec.toFixed(5), fov: "0.05", det: (rec?.bp_rp ?? 2) > 3 || cls.key === "bd" ? "6" : "3" } : null;
  const conf = src === "model" ? st.conf[i] / 255 : rec?.confidence;

  return (
    <aside data-tour="card" className="absolute right-3 top-16 bottom-14 z-10 flex w-[380px] max-w-[calc(100%-24px)] flex-col overflow-hidden rounded-md border border-teal-500/25 bg-slate-950/85 backdrop-blur-md">
      <div className="flex items-start gap-2 border-b border-teal-500/20 p-3">
        <span className="mt-1 h-3 w-3 shrink-0 rounded-full" style={{ background: cls.color, boxShadow: `0 0 10px ${cls.color}` }} />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold text-white" title={title}>{title}</h2>
          <div className="text-[11px] text-slate-400">{cls.label} · {fmtDist(dist)}</div>
        </div>
        <button onClick={onFly} className="rounded border border-slate-700 px-2 py-0.5 text-[11px] hover:border-teal-300" title="Fly here (F)">Fly</button>
        <button onClick={onClose} className="rounded border border-slate-700 px-2 py-0.5 text-[11px] hover:border-teal-300" aria-label="Close">✕</button>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto p-3 text-xs">
        <Section title="Classification">
          <div className="rounded border border-slate-800 bg-slate-900/60 p-2">
            <div className="flex items-center justify-between">
              <b style={{ color: cls.color }}>{cls.label}</b>
              <span className="rounded px-1.5 py-0.5 text-[10px]" style={{ background: `${SRC_SWATCH[src]}22`, color: SRC_SWATCH[src] }}>{SOURCE_LABEL[src]}</span>
            </div>
            <p className="mt-1 text-slate-400">{cls.desc}.</p>
            {conf != null && src !== "catalogue" && <p className="mt-1">Confidence <b>{Math.round(conf * 100)}%</b>{src === "model" && " (share of the 15 most similar catalogued stars with this type)"}</p>}
            {rec?.sp_type && <p className="mt-1">SIMBAD spectral type <b className="font-mono">{rec.sp_type}</b>{rec.otype && <> · type <span className="font-mono">{rec.otype}</span></>}</p>}
            {src === "none" && <p className="mt-1 text-fuchsia-300">Uncharted: Gaia has no colour for it and no catalogue has a type. A SPHEREx spectrum can classify it.</p>}
          </div>
        </Section>

        <Section title="SPHEREx DR1 + QR3 spectrum" tour="spectrum">
          {spx ? <SpectrumView s={spx} classes={data.meta.classes} mapClass={cls.id} /> : (
            <div className="space-y-2">
              <p className="text-slate-400">Measure this object's 0.75–5 µm spectrum from every SPHEREx image of it (all six detectors, DR1 + QR3), then classify it from the spectrum.</p>
              <button disabled={measuring || !rec} onClick={measure}
                className="w-full rounded border border-sky-400/60 bg-sky-500/15 py-2 font-semibold text-sky-100 hover:bg-sky-500/25 disabled:opacity-50">
                {measuring ? `Measuring… ${elapsed.toFixed(0)} s (first visit downloads every image, 1–3 min)` : "Measure SPHEREx spectrum"}
              </button>
            </div>
          )}
        </Section>

        <Section title="2D view: SPHEREx images">
          {sky ?? <p className="text-slate-400">The SPHEREx box beside this card shows the all-sky image around it; <b>Details</b> there (or the button below) opens every SPHEREx image of it.</p>}
          {exploreQuery && (
            <button onClick={() => onExplore(exploreQuery)} data-tour="explore-button" className="mt-2 w-full rounded border border-amber-400/60 bg-amber-500/10 py-2 font-semibold text-amber-100 hover:bg-amber-500/20">
              Open SPHEREx images (time-lapse, spectra) →
            </button>
          )}
        </Section>

        {system && <SystemView sys={system} />}

        <Section title="Measurements">
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[11px]">
            <Row k="Distance" v={fmtDist(dist)} />
            <Row k="Parallax" v={rec?.parallax_mas != null ? `${rec.parallax_mas.toFixed(2)} mas` : "–"} />
            <Row k="RA, Dec" v={ra != null && dec != null ? `${ra.toFixed(4)}°, ${dec.toFixed(4)}°` : "–"} />
            <Row k="Galactic xyz" v={xyz.map((v) => v.toFixed(1)).join(", ") + " pc"} />
            <Row k="G mag" v={rec?.gmag != null ? rec.gmag.toFixed(2) : "–"} />
            <Row k="Abs. G" v={rec?.abs_g != null ? rec.abs_g.toFixed(2) : (bpRpOf(st.attrs, i) != null ? absMagOf(st.attrs, i).toFixed(1) : "–")} />
            <Row k="BP−RP" v={rec?.bp_rp != null ? rec.bp_rp.toFixed(2) : "–"} />
            <Row k="Teff (Gaia)" v={rec?.teff_gspphot != null ? `${Math.round(rec.teff_gspphot)} K` : "–"} />
            <Row k="Proper motion" v={rec?.pmra != null && rec.pmdec != null ? `${Math.hypot(rec.pmra, rec.pmdec).toFixed(0)} mas/yr` : "–"} />
            <Row k="v tangential" v={rec?.v_tan_km_s != null ? `${rec.v_tan_km_s.toFixed(1)} km/s` : "–"} />
            <Row k="v radial" v={rec?.radial_velocity != null ? `${rec.radial_velocity.toFixed(1)} km/s` : "–"} />
            <Row k="Gaia DR3" v={rec?.gaia_dr3_id ?? (rec ? "not in Gaia" : "–")} />
          </dl>
          {err && <p className="mt-2 text-red-300">{err}</p>}
        </Section>
      </div>
    </aside>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <><dt className="text-slate-500">{k}</dt><dd className="truncate text-right text-slate-200" title={v}>{v}</dd></>;
}

function SpectrumView({ s, classes, mapClass }: { s: SpherexSpectrum; classes: { id: number; label: string; color: string }[]; mapClass?: number }) {
  if (s.status !== "ok" || !s.spectrum.length) {
    const why = { saturated: "The star is too bright: its core is saturated in most SPHEREx frames, so no reliable spectrum.",
      too_faint: "Too faint: fewer than eight channels have a usable measurement.", no_data: "SPHEREx has not observed this position yet." }[s.status as "saturated"] ?? s.status;
    return <p className="rounded border border-slate-800 bg-slate-900/60 p-2 text-slate-400">{why} ({s.n_frames} frames, {s.releases.join("+") || "DR1+QR3"})</p>;
  }
  const W = 340, H = 150, pad = { l: 34, r: 8, t: 8, b: 22 };
  const pts = s.spectrum.filter((p) => p.f != null && Number.isFinite(p.f));
  const lw = (w: number) => pad.l + ((Math.log(w) - Math.log(0.74)) / (Math.log(5.05) - Math.log(0.74))) * (W - pad.l - pad.r);
  const fmax = Math.max(...pts.map((p) => p.f + (p.e ?? 0))) * 1.08;
  const fmin = Math.min(0, ...pts.map((p) => p.f - (p.e ?? 0)));
  const y = (f: number) => pad.t + (1 - (f - fmin) / (fmax - fmin)) * (H - pad.t - pad.b);
  const c = s.classification;
  const model = c?.bb_model;
  const cc = c?.class_id != null ? classes[c.class_id] : null;
  const bands = [[1.33, 1.45, "H₂O"], [3.2, 3.4, "CH₄"], [4.2, 4.4, "CO₂"], [4.5, 4.8, "CO"]] as const;
  return (
    <div className="space-y-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded border border-slate-800 bg-slate-950">
        {bands.map(([a, b, l]) => (
          <g key={l}><rect x={lw(a)} y={pad.t} width={lw(b) - lw(a)} height={H - pad.t - pad.b} fill="#38bdf8" opacity={0.07} />
            <text x={(lw(a) + lw(b)) / 2} y={pad.t + 9} fontSize={8} fill="#7dd3fc" textAnchor="middle">{l}</text></g>
        ))}
        {[0.8, 1, 1.5, 2, 3, 4, 5].map((w) => (
          <g key={w}><line x1={lw(w)} x2={lw(w)} y1={H - pad.b} y2={H - pad.b + 3} stroke="#475569" />
            <text x={lw(w)} y={H - 6} fontSize={8} fill="#64748b" textAnchor="middle">{w}</text></g>
        ))}
        <text x={W - pad.r} y={H - 6} fontSize={8} fill="#64748b" textAnchor="end">µm</text>
        <text x={4} y={pad.t + 6} fontSize={8} fill="#64748b">mJy</text>
        {model && <polyline fill="none" stroke="#f59e0b" strokeWidth={1.2} strokeDasharray="3 2" opacity={0.8}
          points={s.spectrum.map((p, k) => (model[k] != null ? `${lw(p.w)},${y(model[k])}` : "")).join(" ")} />}
        {pts.map((p, k) => (
          <g key={k}>
            {p.e != null && <line x1={lw(p.w)} x2={lw(p.w)} y1={y(p.f - p.e)} y2={y(p.f + p.e)} stroke="#38bdf8" opacity={0.4} />}
            <circle cx={lw(p.w)} cy={y(p.f)} r={1.8} fill="#e0f2fe" />
          </g>
        ))}
      </svg>
      {c && (
        <div className="rounded border border-sky-500/30 bg-sky-950/30 p-2">
          <div className="flex items-center justify-between"><b style={{ color: cc?.color }}>{c.label}</b><span className="font-mono">{Math.round(c.confidence * 100)}% conf.</span></div>
          {c.features && c.features.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{c.features.map((f) => <span key={f} className="rounded bg-sky-500/20 px-1.5 text-[10px] text-sky-100">{f}</span>)}</div>}
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] text-slate-300">{c.evidence.map((e) => <li key={e}>{e}</li>)}</ul>
          {mapClass != null && c.class_id != null && c.class_id !== mapClass && (
            <p className="mt-1 text-[11px] text-amber-200/90">
              The map keeps the catalogue class ({classes[mapClass]?.label}). A 0.75–5 µm colour temperature cannot tell a white dwarf or a giant
              from a main-sequence star of the same temperature; SPHEREx only overrides a class when no catalogue gave one, or when it sees brown-dwarf molecular bands.
            </p>
          )}
        </div>
      )}
      <p className="text-[10px] text-slate-500">{s.n_good} of {s.n_frames} frames used · {s.releases.join(" + ")} · detectors {s.detectors.join(",")} · {s.n_epochs} epochs. Dashed: blackbody fit. Forced PSF photometry, not a calibrated spectrum.</p>
    </div>
  );
}

function SystemView({ sys }: { sys: NonNullable<GalaxyData["systems"]>[number] }) {
  const jd = jdNow();
  const rows = sys.planets.map((p, k) => ({ p, o: orbitFor(p, sys.st_mass, k) }));
  const pm = (v: number | null | undefined, e: number | null | undefined, d: number) =>
    v == null ? "–" : e != null && e > 0 ? `${v.toFixed(d)}±${e < 10 ** -d ? e.toPrecision(1) : e.toFixed(d)}` : v.toFixed(d);
  return (
    <Section title={`Planetary system · ${sys.planets.length} planet${sys.planets.length > 1 ? "s" : ""}`}>
      <p className="mb-1 text-slate-400">Zoom in (or press Fly) to see the orbits to scale. Elements from the NASA Exoplanet Archive; ± is 1σ. Values marked * are derived with Kepler's third law from the star's mass.</p>
      <table className="w-full font-mono text-[10px]">
        <thead className="text-slate-500"><tr><th className="text-left">Planet</th><th className="text-right">P (d)</th><th className="text-right">a (AU)</th><th className="text-right">e</th><th className="text-right">R⊕</th></tr></thead>
        <tbody>{rows.map(({ p, o }) => (
          <tr key={p.name} className="border-t border-slate-800/70" title={o ? `Position today: ${phaseNote(o, jd)}` : "Not drawn: the archive has neither its period nor its orbit size"}>
            <td className="truncate py-0.5 text-slate-200">{p.name}{!o && <span className="text-slate-500"> (not drawn)</span>}</td>
            <td className="text-right">{o?.P_source === "kepler3" ? `${o.P.toFixed(2)}*` : pm(p.period_d, p.period_err, p.period_d != null && p.period_d < 10 ? 4 : 2)}</td>
            <td className="text-right">{o?.a_source === "kepler3" ? `${o.a.toFixed(4)}*` : pm(p.a_au, p.a_err, 4)}</td>
            <td className="text-right">{pm(p.ecc, p.ecc_err, 2)}</td>
            <td className="text-right">{p.radius_re != null ? p.radius_re.toFixed(1) : "–"}</td>
          </tr>
        ))}</tbody>
      </table>
      <ul className="mt-1.5 space-y-0.5 text-[10px] text-slate-500">
        {rows.filter((r) => r.o).map(({ p, o }) => <li key={p.name}><span className="text-slate-300">{p.name.replace(sys.host, "").trim() || p.name}</span>: {phaseNote(o!, jd)}</li>)}
      </ul>
    </Section>
  );
}

/** What the orbit animation is, shown whenever a planetary system is on screen. */
function OrbitLegend({ jd, unplotted }: { jd: number; unplotted: string[] }) {
  const date = new Date((jd - 2440587.5) * 86400000).toISOString().slice(0, 10);
  return (
    <div className="pointer-events-none absolute left-1/2 top-16 z-10 w-[min(560px,calc(100%-24px))] -translate-x-1/2 rounded-md border border-sky-400/30 bg-slate-950/85 px-3 py-2 font-mono text-[10px] leading-relaxed text-slate-300 backdrop-blur">
      <div className="flex justify-between gap-2 tracking-[0.2em] text-sky-300"><span>ORBIT PREDICTION · NOT AN OBSERVATION</span><span className="tracking-normal text-slate-200">{date}</span></div>
      <div><b className="text-slate-100">Time:</b> 1 s = 1 day for every system (Mercury would take 88 s), starting today.</div>
      <div><b className="text-slate-100">Motion:</b> two-body Kepler orbits from NASA Exoplanet Archive elements (P, a, e, ω); planet–planet gravity ignored.</div>
      <div><b className="text-slate-100">Start:</b> positions predicted from each planet's transit or periastron time; <span className="text-amber-300">yellow arc = ±1σ</span>. Faint planets have no measured phase and are placed arbitrarily.</div>
      <div><b className="text-slate-100">Not known, drawn for display:</b> orbit tilt on the sky (drawn flat, transit side towards the Sun) and planet dots (markers, not to scale). Orbit sizes and the star are to scale; green = estimated habitable zone.</div>
      {unplotted.length > 0 && <div className="text-slate-500">Not drawn (no period or orbit size): {unplotted.join(", ")}</div>}
    </div>
  );
}

// Aladin Lite preview of the SPHEREx DR1 all-sky HiPS cube (tiles load straight from IRSA; the slider picks the channel).


/** True on screens wide enough to float the sky box beside the object card (left panel + box + card). */
function useWide(): boolean {
  const q = "(min-width: 1180px)";
  const [wide, setWide] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const f = () => setWide(m.matches);
    m.addEventListener("change", f);
    return () => m.removeEventListener("change", f);
  }, []);
  return wide;
}
