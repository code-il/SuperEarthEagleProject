import { useEffect, useRef, useState } from "react";

/**
 * The real observed sky (HiPS all-sky surveys streamed from CDS via Aladin Lite), in Galactic
 * coordinates so the Milky Way runs across the middle. Shown first as a small glass box with a
 * full-sky map (a slowly turning globe on the Globe view); the expand button opens it in a large window (Esc or ✕ closes).
 *
 * Aladin Lite is only downloaded when this box mounts (dynamic import).
 */

// Each survey lists mirrors in order of preference; the first that answers is used
// (Aladin's own ID lookup goes through a single CDS service, so we pass tile URLs directly).
const SURVEYS = [
  { id: "iris", label: "Far-infrared", note: "IRAS 25–100 µm (IRIS): warm dust in the Galactic plane",
    urls: ["https://irsa.ipac.caltech.edu/data/hips/CDS/IRIS/color", "https://alasky.cds.unistra.fr/IRISColor"] },
  { id: "allwise", label: "Mid-infrared", note: "AllWISE 3.4–22 µm: stars and hot dust, close to SPHEREx's range",
    urls: ["https://irsa.ipac.caltech.edu/data/hips/CDS/AllWISE/RGB-W4-W2-W1", "https://alasky.cds.unistra.fr/AllWISE/RGB-W4-W2-W1"] },
  { id: "dss", label: "Optical", note: "DSS2 colour: the sky as optical photographic plates see it",
    urls: ["https://irsa.ipac.caltech.edu/data/hips/CDS/DSS2/color", "https://alasky.cds.unistra.fr/DSS/DSSColor"] },
] as const;

/** First mirror whose HiPS properties file answers within a few seconds. */
async function pickMirror(urls: readonly string[]): Promise<string> {
  for (const u of urls) {
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 6000);
      const r = await fetch(`${u}/properties`, { signal: ctl.signal });
      clearTimeout(t);
      if (r.ok) return u;
    } catch { /* try the next mirror */ }
  }
  throw new Error("no survey mirror is reachable right now");
}
type SurveyId = (typeof SURVEYS)[number]["id"];

const VIEWS = [
  { id: "SIN", label: "Globe", fov: 180 },
  { id: "MOL", label: "Full sky", fov: 360 },
] as const;
type ViewId = (typeof VIEWS)[number]["id"];

interface AladinApi {
  setImageLayer(layer: unknown): void;
  setProjection(p: string): void;
  gotoRaDec(ra: number, dec: number): void;
  setFoV(deg: number): void;
  getFov(): [number, number];
}
type ALib = { init: Promise<void>; aladin(el: HTMLElement, opts: Record<string, unknown>): AladinApi; imageHiPS(url: string, opts?: Record<string, unknown>): unknown };

/** Galactic (l, b) in degrees → ICRS (ra, dec) in degrees. */
function galToIcrs(l: number, b: number): [number, number] {
  const d = Math.PI / 180;
  const g = [Math.cos(b * d) * Math.cos(l * d), Math.cos(b * d) * Math.sin(l * d), Math.sin(b * d)];
  // transpose of the ICRS → Galactic rotation matrix
  const M = [
    [-0.0548755604, 0.4941094279, -0.867666149],
    [-0.8734370902, -0.44482963, -0.1980763734],
    [-0.4838350155, 0.7469822445, 0.4559837762],
  ];
  const [x, y, z] = M.map((r) => r[0] * g[0] + r[1] * g[1] + r[2] * g[2]);
  return [((Math.atan2(y, x) / d) + 360) % 360, Math.asin(z) / d];
}

export function ObservedSky({ className = "" }: { className?: string }) {
  const host = useRef<HTMLDivElement>(null);
  const al = useRef<AladinApi | null>(null);
  const lib = useRef<ALib | null>(null);
  const firstLayer = useRef<string | null>(null);
  const spin = useRef({ l: 0, on: !window.matchMedia("(prefers-reduced-motion: reduce)").matches });
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [survey, setSurvey] = useState<SurveyId>(SURVEYS[0].id);
  const [view, setView] = useState<ViewId>("MOL");

  // Load Aladin Lite and create the view.
  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const mod = await import("aladin-lite");
        const A = (mod.default ?? mod) as unknown as ALib;
        // Pick the first survey's mirror before creating the view, so Aladin never falls back to
        // its own default survey (whose server may be unreachable and would replace our layer).
        const [, firstUrl] = await Promise.all([A.init, pickMirror(SURVEYS[0].urls)]);
        if (dead || !host.current) return;
        firstLayer.current = firstUrl;
        const [ra, dec] = galToIcrs(0, 0);
        const a = A.aladin(host.current, {
          survey: firstUrl, projection: "MOL", cooFrame: "galactic", fov: 360, target: `${ra} ${dec}`,
          showReticle: false, showCooGrid: false, showLayersControl: false, showFullscreenControl: false, showProjectionControl: false,
          showSettingsControl: false, showShareControl: false, showSimbadPointerControl: false, showFrame: false, showContextMenu: false,
          showCooLocation: false, showZoomControl: false, showGotoControl: false, showCooGridControl: false, showFov: false, showStatusBar: false,
          backgroundColor: "rgb(0,0,0)",
        });
        al.current = a;
        lib.current = A;
        setReady(true);
      } catch (e) {
        setError((e as Error).message || "Could not load the sky viewer");
      }
    })();
    return () => { dead = true; };
  }, []);

  // Slowly turn the globe along the Galactic plane until the user touches it.
  useEffect(() => {
    if (!ready || view !== "SIN" || expanded || !spin.current.on) return;
    let raf = 0, prev = performance.now();
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (now - prev < 50) return; // ~20 fps is plenty for a slow turn
      spin.current.l = (spin.current.l + ((now - prev) / 1000) * 4) % 360;
      prev = now;
      const [ra, dec] = galToIcrs(spin.current.l, 0);
      al.current?.gotoRaDec(ra, dec);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [ready, view, expanded]);

  useEffect(() => {
    if (!ready) return;
    let dead = false;
    const sv = SURVEYS.find((x) => x.id === survey)!;
    if (sv.id === SURVEYS[0].id && firstLayer.current) return; // already the base layer from init
    firstLayer.current = null;
    pickMirror(sv.urls)
      .then((url) => {
        if (dead) return;
        al.current!.setImageLayer(lib.current!.imageHiPS(url, { name: sv.label, imgFormat: "jpeg" }));
        setError(null);
      })
      .catch((e) => { if (!dead) setError((e as Error).message); });
    return () => { dead = true; };
  }, [survey, ready]);

  useEffect(() => {
    if (!ready) return;
    const v = VIEWS.find((x) => x.id === view)!;
    try {
      al.current!.setProjection(view);
      al.current!.setFoV(v.fov);
    } catch (e) { setError((e as Error).message); }
  }, [view, ready]);

  // Expanded window: Esc closes, page scroll locked.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setExpanded(false); };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = prevOverflow; };
  }, [expanded]);

  // In the small box, the mouse wheel scrolls the page instead of zooming the sky.
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => { if (!expanded) e.stopPropagation(); };
    el.addEventListener("wheel", onWheel, { capture: true });
    return () => el.removeEventListener("wheel", onWheel, { capture: true });
  }, [expanded]);

  const stopSpin = () => { spin.current.on = false; };
  const zoom = (k: number) => {
    const a = al.current;
    if (!a) return;
    a.setFoV(Math.min(360, Math.max(0.5, a.getFov()[0] * k)));
  };
  const s = SURVEYS.find((x) => x.id === survey)!;

  const chip = (on: boolean) => `rounded-full px-2.5 py-1 text-[11px] transition-colors ${on ? "bg-white/15 text-white" : "text-neutral-400 hover:text-white"}`;

  return (
    <div className={`flex min-w-0 ${className}`}>
      {expanded && <div className="fixed inset-0 z-[55] bg-black/70 backdrop-blur-sm" onClick={() => setExpanded(false)} aria-hidden />}
      <section
        aria-label="The observed sky"
        className={expanded
          ? "glass fixed inset-3 z-[60] flex flex-col p-3 sm:inset-8 sm:p-4"
          : "glass flex min-w-0 flex-1 flex-col p-3"}
      >
        <header className="flex flex-wrap items-center gap-2 px-1 pb-2">
          <div className="mr-auto min-w-0">
            <span className="font-mono text-xs text-neutral-500">Observed sky</span>
            {expanded && <h2 className="text-lg font-medium tracking-tight text-white">The whole sky, in Galactic coordinates</h2>}
          </div>
          <div role="radiogroup" aria-label="View" className="flex rounded-full bg-black/30 p-0.5">
            {VIEWS.map((v) => (
              <button key={v.id} type="button" role="radio" aria-checked={view === v.id} className={chip(view === v.id)} onClick={() => { stopSpin(); setView(v.id); }}>{v.label}</button>
            ))}
          </div>
          <button type="button" onClick={() => setExpanded(!expanded)}
            aria-label={expanded ? "Close the enlarged sky view" : "Expand the sky view"} title={expanded ? "Close (Esc)" : "Expand"}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-white/10 text-neutral-200 transition-colors hover:bg-white/20 hover:text-white">
            {expanded ? (
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden><path d="M3 3l10 10M13 3L3 13" /></svg>
            ) : (
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden><path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5L9 7M2.5 13.5L7 9" /></svg>
            )}
          </button>
        </header>

        <div className={`relative overflow-hidden rounded-[14px] bg-black ${expanded ? "min-h-0 flex-1" : "aspect-[2/1] w-full xl:aspect-auto xl:min-h-[200px] xl:flex-1"}`} onPointerDown={stopSpin}>
          <div ref={host} style={{ width: "100%", height: "100%" }} />
          {!ready && !error && <div className="absolute inset-0 flex items-center justify-center font-mono text-[11px] text-neutral-500">loading the sky…</div>}
          {error && <div className="absolute inset-0 flex items-center justify-center p-4 text-center text-xs text-neutral-400">Sky survey unavailable: {error}</div>}
          {expanded && ready && (
            <div className="absolute bottom-3 left-3 flex flex-col gap-1">
              <button type="button" aria-label="Zoom in" onClick={() => zoom(0.6)} className="glass glass-hover h-9 w-9 rounded-full text-lg text-white">+</button>
              <button type="button" aria-label="Zoom out" onClick={() => zoom(1 / 0.6)} className="glass glass-hover h-9 w-9 rounded-full text-lg text-white">−</button>
            </div>
          )}
        </div>

        <footer className="flex flex-wrap items-center gap-2 px-1 pt-2">
          <div role="radiogroup" aria-label="Survey" className="flex rounded-full bg-black/30 p-0.5">
            {SURVEYS.map((x) => (
              <button key={x.id} type="button" role="radio" aria-checked={survey === x.id} title={x.note} className={chip(survey === x.id)} onClick={() => setSurvey(x.id)}>{x.label}</button>
            ))}
          </div>
          <span className={`min-w-0 truncate text-[11px] text-neutral-500 ${expanded ? "" : "hidden xl:inline"}`}>{s.note}</span>
          {expanded && <span className="ml-auto text-[11px] text-neutral-500">Drag to turn · scroll to zoom · HiPS surveys via IRSA / CDS</span>}
        </footer>
      </section>
    </div>
  );
}
