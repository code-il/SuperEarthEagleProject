/**
 * The SPHEREx time-series viewer. It opens as a pop-up over the 3D Atlas (``/?view=explore&ra=…``);
 * ``/explore?…`` links redirect there. ``embedded`` + ``onClose`` are set by the Atlas.
 *
 *  ┌──────────────── top bar: search, FOV, detectors ────────────────┐
 *  │ left: mode, channels,   │ centre: viewer(s)    │ right: metadata, │
 *  │ display, overlays       │ + playback timeline  │ target, quality  │
 *  ├──────────────── bottom: coverage / light curve / spectrum ──────┤
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import type { Channel, DisplaySettings, ImagePlane, Marker, OverlaySettings, PixelReadout, ViewMode, ViewState } from "../types";
import { api, apiUrl } from "../lib/api";
import type { ExploreParams } from "../lib/api";
import { EXPLORE_KEYS, buildUrl, exploreSearch, parseUrl } from "../lib/urlState";
import { DEFAULT_DISPLAY, limitsFor } from "../lib/render";
import { frameDurationMs, formatDate, formatDateTime, formatDuration } from "../lib/time";
import { formatAngle, formatDecDms, formatRaHms } from "../lib/coords";
import { propagate } from "../lib/catalog";
import { makeTan, pixelScaleArcsec } from "../lib/wcs";
import { autoReference, nearestInChannel, nearestVisible, visitIds } from "../lib/selection";
import { paramsKey, useSeries, usePlanes, type Frame } from "../state/useSeries";
import { useAnalysis, useCatalog, useCompare, usePlane, useSourceProducts, useStacks } from "../state/useProducts";
import { AnalysisPanel } from "../components/AnalysisPanel";
import { SkyCanvas, Spinner } from "../components/SkyCanvas";
import { PlaybackBar, type Timing } from "../components/PlaybackBar";
import type { TimeAxis } from "../components/Timeline";
import { SearchBar } from "../components/SearchBar";
import { ModePanel, DEFAULT_COMPARE, DEFAULT_STACK, type CompareOptions, type StackOptions } from "../components/ModePanel";
import { ALL_CHANNELS, ChannelSelector, channelFilterMatches, type ChannelFilter } from "../components/ChannelSelector";
import { DisplayControls } from "../components/DisplayControls";
import { OverlayControls } from "../components/OverlayControls";
import { ObservationPanel, QualityPanel, ReadoutPanel, TargetPanel } from "../components/InfoPanels";
import { AladinContext } from "../components/AladinContext";
import { PlotsPanel } from "../components/PlotsPanel";
import { Note, Term } from "../components/ui";

const DEFAULT_OVERLAYS: OverlaySettings = { target: true, gaia: false, catwise: false, sources: true, labels: true, compass: true, maxMag: 18 };

function caption(f?: Frame): string | undefined {
  if (!f) return undefined;
  return `${formatDateTime(f.observation_time)} · ${f.wavelength.toFixed(3)} µm · D${f.detector}`;
}

export default function ExplorePage({ embedded = false, onClose }: { embedded?: boolean; onClose?: () => void } = {}) {
  const [sp, setSp] = useSearchParams();
  const navigate = useNavigate();
  const openSearch = (p: ExploreParams) => {
    if (!embedded) return navigate(buildUrl(p, { mode }));
    const next = new URLSearchParams(sp);
    for (const k of EXPLORE_KEYS) next.delete(k);
    exploreSearch(p, { mode }).forEach((v, k) => next.set(k, v));
    setSp(next);
  };
  const url = useMemo(() => parseUrl(sp), [sp]);
  const pkey = paramsKey(url.params);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const params = useMemo<ExploreParams | null>(() => url.params, [pkey]);
  const series = useSeries(params);
  const { frames } = series;
  const grid = series.processed?.grid ?? null;

  // ---- UI state -----------------------------------------------------------
  const [mode, setMode] = useState<ViewMode>(url.mode ?? "single");
  const [filter, setFilter] = useState<ChannelFilter>(ALL_CHANNELS);
  const [cur, setCur] = useState<number | null>(null); // seriesIndex of frame A
  const [pinnedB, setPinnedB] = useState<string | null>(null);
  const [compareChannel, setCompareChannel] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [fps, setFps] = useState(3);
  const [timing, setTiming] = useState<Timing>("uniform");
  const [loop, setLoop] = useState(true);
  const [axis, setAxis] = useState<TimeAxis>("visits");
  const [blinkMs, setBlinkMs] = useState(600);
  const [blinkB, setBlinkB] = useState(false);
  const [display, setDisplay] = useState<DisplaySettings>({ ...DEFAULT_DISPLAY, ...(url.stretch ? { stretch: url.stretch } : {}), ...(url.cmap ? { colormap: url.cmap } : {}) });
  const [overlays, setOverlays] = useState<OverlaySettings>(DEFAULT_OVERLAYS);
  const [view, setView] = useState<ViewState | null>(null);
  const [hover, setHover] = useState<PixelReadout | null>(null);
  const [compareOpts, setCompareOpts] = useState<CompareOptions>(DEFAULT_COMPARE);
  const [stackOpts, setStackOpts] = useState<StackOptions>(DEFAULT_STACK);
  const [stackIdx, setStackIdx] = useState(0);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [copied, setCopied] = useState(false);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);

  useEffect(() => {
    api.channels().then(setChannels).catch(() => setChannels([]));
  }, []);

  // New field: reset per-field state; open at ?obs= if given.
  useEffect(() => {
    setView(null);
    setPinnedB(null);
    setCompareChannel(null);
    setFilter(ALL_CHANNELS);
    setStackIdx(0);
    setPlaying(false);
    setSelectedSource(null);
    if (frames.length) {
      const fromUrl = url.obs ? frames.find((f) => f.observation_id === url.obs) : undefined;
      setCur((fromUrl ?? frames[0]).seriesIndex);
    } else setCur(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frames]);

  const visible = useMemo(() => frames.filter((f) => channelFilterMatches(filter, f.channel?.index, f.wavelength)), [frames, filter]);
  const visibleSet = useMemo(() => new Set(visible.map((f) => f.seriesIndex)), [visible]);

  // Keep A visible when the channel filter changes (same moment, nearest frame).
  useEffect(() => {
    if (cur === null || !visible.length || visibleSet.has(cur)) return;
    const f = nearestVisible(visible, frames[cur]?.mjd ?? 0);
    if (f) setCur(f.seriesIndex);
  }, [visible, visibleSet, cur, frames]);

  const vIndex = cur === null ? -1 : visible.findIndex((f) => f.seriesIndex === cur);
  const A: Frame | undefined = vIndex >= 0 ? visible[vIndex] : undefined;
  const B: Frame | undefined = useMemo(() => {
    if (compareChannel !== null) return nearestInChannel(frames, A, compareChannel);
    if (pinnedB) return frames.find((f) => f.observation_id === pinnedB);
    return autoReference(visible, A);
  }, [compareChannel, pinnedB, frames, visible, A]);

  const { planes, failed } = usePlanes(frames, cur ?? 0, display.plane ?? "SCI");
  const planeA = A ? planes.get(A.seriesIndex) ?? null : null;
  const planeB = B ? planes.get(B.seriesIndex) ?? null : null;

  // ---- display limits -----------------------------------------------------
  const seriesLimits = useMemo(() => {
    const out = new Map<number, [number, number]>();
    if (display.limitsMode === "frame") return out;
    const byDet = new Map<number, ImagePlane[]>();
    for (const f of visible) {
      const pl = planes.get(f.seriesIndex);
      const k = limitGroup(f, display.limitsMode);
      if (pl) byDet.set(k, [...(byDet.get(k) ?? []), pl]);
    }
    for (const [d, pls] of byDet) out.set(d, limitsFor(pls, display.percentiles));
    return out;
    // Recompute when roughly every 10% more frames have loaded (cheap enough, avoids flicker).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, Math.floor(planes.size / Math.max(1, Math.ceil(frames.length / 10))), display.limitsMode, display.percentiles]);

  const limitsOf = useCallback((f: Frame | undefined, pl: ImagePlane | null): [number, number] => {
    if (!f) return [0, 1];
    if (display.limitsMode !== "frame") {
      const l = seriesLimits.get(limitGroup(f, display.limitsMode));
      if (l) return l;
      if (f.display_limits && display.limitsMode === "channel" && (display.plane ?? "SCI") === "SCI") return f.display_limits;
    }
    return pl ? limitsFor([pl], display.percentiles) : f.display_limits ?? [0, 1];
  }, [display.limitsMode, display.percentiles, seriesLimits]);

  // ---- playback -----------------------------------------------------------
  const visibleMjds = useMemo(() => visible.map((f) => f.mjd), [visible]);
  const goto = useCallback((i: number) => {
    const f = visible[Math.max(0, Math.min(visible.length - 1, i))];
    if (f) setCur(f.seriesIndex);
  }, [visible]);

  const planesRef = useRef(planes);
  planesRef.current = planes;
  useEffect(() => {
    if (!playing || vIndex < 0 || mode === "stacked") return;
    let t: ReturnType<typeof setTimeout>;
    const advance = () => {
      let next = vIndex + 1;
      if (next >= visible.length) {
        if (!loop) {
          setPlaying(false);
          return;
        }
        next = 0;
      }
      // Wait for the next image rather than skipping it.
      if (!planesRef.current.has(visible[next].seriesIndex) && !failed.has(visible[next].seriesIndex)) {
        t = setTimeout(advance, 100);
        return;
      }
      setCur(visible[next].seriesIndex);
    };
    t = setTimeout(advance, frameDurationMs(visibleMjds, vIndex, fps, timing));
    return () => clearTimeout(t);
  }, [playing, vIndex, visible, visibleMjds, fps, timing, loop, failed, mode]);

  // Blink between A and B.
  useEffect(() => {
    if (mode !== "blink") {
      setBlinkB(false);
      return;
    }
    const t = setInterval(() => setBlinkB((b) => !b), blinkMs);
    return () => clearInterval(t);
  }, [mode, blinkMs]);

  // Keep the URL shareable (current observation + mode), without reloading data.
  useEffect(() => {
    if (!params || playing) return;
    const next = new URLSearchParams(sp);
    if (A) next.set("obs", A.observation_id);
    if (mode !== "single") next.set("mode", mode);
    else next.delete("mode");
    if (next.toString() !== sp.toString()) setSp(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [A?.observation_id, mode, playing]);

  // Keyboard shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if (e.key === " ") {
        setPlaying((p) => !p);
        e.preventDefault();
      } else if (e.key === "ArrowRight") goto(vIndex + 1);
      else if (e.key === "ArrowLeft") goto(vIndex - 1);
      else if (e.key === "Home") goto(0);
      else if (e.key === "End") goto(visible.length - 1);
      else if (e.key === "0" || e.key.toLowerCase() === "r") setView(null);
      else if (e.key === "+" || e.key === "=") setView((v) => (v ? { ...v, zoom: v.zoom * 1.25 } : v));
      else if (e.key === "-") setView((v) => (v ? { ...v, zoom: v.zoom / 1.25 } : v));
      else if (e.key.toLowerCase() === "b" && A) setPinnedB(A.observation_id);
      else return;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goto, vIndex, visible.length, A]);

  // ---- backend products for comparison modes ---------------------------------
  const isCmp = mode === "difference" || mode === "ratio";
  const aIds = useMemo(() => (A ? (compareOpts.scopeA === "visit" ? visitIds(visible, A) : [A.observation_id]) : []), [A, visible, compareOpts.scopeA]);
  const bIds = useMemo(() => (B ? (compareOpts.scopeB === "visit" ? visitIds(visible, B) : [B.observation_id]) : []), [B, visible, compareOpts.scopeB]);
  const cmp = useCompare(params, isCmp && !playing, mode === "ratio" ? "ratio" : "difference", aIds, bIds, compareOpts);
  const stacks = useStacks(params, mode === "stacked", stackOpts);
  const stack = stacks.data?.[Math.min(stackIdx, (stacks.data?.length ?? 1) - 1)];
  const stackPlane = usePlane(mode === "stacked" ? stack?.fits_url : null);

  // ---- overlays ---------------------------------------------------------------
  const gaia = useCatalog("gaia", grid, overlays.gaia);
  const catwise = useCatalog("catwise", grid, overlays.catwise);
  const analysisTarget = series.processed?.query?.kind === "name" ? series.processed?.query?.query : undefined;
  const analysis = useAnalysis(grid, analysisTarget);
  const sourceProducts = useSourceProducts(selectedSource, grid, analysisTarget);
  const plotCurves = useMemo(() => {
    const base = analysis.data?.light_curves ?? [];
    const lc = sourceProducts.data?.light_curve;
    return lc ? [lc, ...base.filter((c) => c.source_id !== lc.source_id)] : base;
  }, [analysis.data, sourceProducts.data]);
  const plotSpectra = sourceProducts.data?.spectra.length ? sourceProducts.data.spectra : analysis.data?.spectra ?? [];
  const availability = useMemo(() => {
    if (!frames.length) return null;
    const visits = new Set(frames.map((f) => f.epoch)).size;
    const releases = [...new Set(frames.map((f) => f.data_release))].join(" + ");
    return { n: frames.length, visits, first: frames[0].observation_time, last: frames[frames.length - 1].observation_time, releases };
  }, [frames]);
  const targetName = (series.processed?.query?.name ?? series.processed?.query?.query ?? "target").replace(/^NAME\s+/, "");

  const markersAt = useCallback((f: Frame | undefined, mjdOverride?: number): Marker[] => {
    const mjd = mjdOverride ?? f?.mjd;
    if (mjd === undefined) return [];
    const out: Marker[] = [];
    if (overlays.gaia && gaia.data) for (const s of gaia.data.sources) {
      if (s.mag !== null && s.mag > overlays.maxMag) continue;
      const [ra, dec] = propagate(s, mjd);
      out.push({ id: s.id, ra, dec, kind: "gaia", mag: s.mag, label: s.id, title: `G = ${s.mag?.toFixed(2) ?? "?"}${s.pm_dec_mas_yr != null ? ` · µ = ${(Math.hypot(s.pm_ra_cosdec_mas_yr ?? 0, s.pm_dec_mas_yr) / 1000).toFixed(2)}″/yr` : ""}` });
    }
    if (overlays.catwise && catwise.data) for (const s of catwise.data.sources) {
      if (s.mag !== null && s.mag > overlays.maxMag) continue;
      const [ra, dec] = propagate(s, mjd);
      const pm = s.pm_dec_mas_yr != null ? Math.hypot(s.pm_ra_cosdec_mas_yr ?? 0, s.pm_dec_mas_yr) / 1000 : null;
      out.push({ id: s.id, ra, dec, kind: "catwise", mag: s.mag, label: s.id, suspect: !!s.pm_suspect,
        title: `W2 = ${s.mag?.toFixed(2) ?? "?"}${pm !== null ? ` · µ = ${pm.toFixed(2)}″/yr` : ""}${s.pm_suspect ? " · motion not applied: probably spurious (next to a saturated star or flagged)" : ""}` });
    }
    if (overlays.sources && analysis.data) for (const s of analysis.data.sources) {
      const p = s.positions?.find((x) => x.observation_id === f?.observation_id);
      const [ra, dec] = p ? [p.ra, p.dec] : propagate(s, mjd);
      out.push({ id: s.id, ra, dec, kind: "source", tone: s.kind, suspect: s.kind === "artifact", label: s.label ?? s.id, selected: s.id === selectedSource,
        title: [s.kind, s.score != null ? `confidence ${s.score}` : "", s.notes].filter(Boolean).join(" · ") });
    }
    if (overlays.target) {
      const t = series.processed?.query?.target;
      const pos = f?.predicted ?? (t && t.pm_dec_mas_yr != null ? propagate({ ...t, ref_mjd: 51544.5 }, mjd) : null);
      if (pos) out.push({ id: "target", ra: pos[0], dec: pos[1], kind: "target", label: targetName, title: "Predicted position on this date (catalogue proper motion)" });
    }
    return out;
  }, [overlays, gaia.data, catwise.data, analysis.data, series.processed, targetName, selectedSource]);

  // ---- derived display ----------------------------------------------------------
  const pixArc = useMemo(() => {
    try {
      return grid ? pixelScaleArcsec(makeTan(grid.wcs)) : null;
    } catch {
      return null;
    }
  }, [grid]);
  const zoomText = view && pixArc ? `${(pixArc / view.zoom).toFixed(2)}″/screen px` : "fit";
  const channelCounts = useMemo(() => {
    const m = new Map<number, number>();
    for (const f of frames) if (f.channel) m.set(f.channel.index, (m.get(f.channel.index) ?? 0) + 1);
    return m;
  }, [frames]);

  const commonCanvas = { display, view, onViewChange: setView, onHover: setHover, showLabels: overlays.labels, showCompass: overlays.compass };

  const loadingSeries = series.status === "searching" || series.status === "processing";

  const viewer = (() => {
    if (!params) return <Welcome error={url.error} embedded={embedded} />;
    if (loadingSeries) return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
        <Spinner label={series.message} />
        <p className="text-xs text-slate-500">{series.elapsed.toFixed(0)} s · first visits to a field download every cutout from IRSA, so this can take a minute or two. Later visits are cached.</p>
      </div>
    );
    if (series.status === "error") return <div className="flex h-full items-center justify-center p-8"><Note tone="error">{series.error}</Note></div>;
    if (!frames.length) return <div className="flex h-full items-center justify-center p-8"><Note tone="warn">{series.message || "No observations."}</Note></div>;
    if (!visible.length) return <div className="flex h-full items-center justify-center p-8"><Note tone="warn">No frames match the channel filter. Click "All" under Spectral channels.</Note></div>;

    const badge = `${vIndex + 1} / ${visible.length}`;
    if (mode === "single" || mode === "blink") {
      const showB = mode === "blink" && blinkB && B;
      const f = showB ? B : A;
      const pl = showB ? planeB : planeA;
      return (
        <div className="relative h-full">
          <SkyCanvas {...commonCanvas} className="h-full w-full" plane={pl} limits={limitsOf(f, pl)} markers={markersAt(f)}
            caption={caption(f)} badge={mode === "blink" ? (showB ? "B" : "A") + " · " + badge : badge}
            loading={!pl && !!f && !failed.has(f.seriesIndex)} fallbackUrl={f?.image_url || null}
            emptyText={f && failed.has(f.seriesIndex) ? `Could not load this frame: ${failed.get(f.seriesIndex)}` : undefined} />
          {mode === "blink" && <div className={`pointer-events-none absolute inset-0 rounded-md border-4 ${showB ? "border-sky-400/70" : "border-amber-400/70"}`} />}
        </div>
      );
    }
    if (mode === "side-by-side") return (
      <div className="grid h-full grid-cols-2 gap-2">
        <div className="relative min-h-0"><SkyCanvas {...commonCanvas} className="h-full w-full border-2 border-amber-400/60" plane={planeA} limits={limitsOf(A, planeA)} markers={markersAt(A)} caption={caption(A)} badge={`A · ${badge}`} loading={!planeA} /></div>
        <div className="relative min-h-0"><SkyCanvas {...commonCanvas} className="h-full w-full border-2 border-sky-400/60" plane={planeB} limits={limitsOf(B, planeB)} markers={markersAt(B)} caption={caption(B)} badge={B && A ? `B · Δt ${formatDuration(B.mjd - A.mjd)}` : "B"} loading={!!B && !planeB} emptyText="No reference frame" /></div>
      </div>
    );
    if (isCmp) {
      const pl = cmp.plane;
      const center = mode === "ratio" && cmp.show === "image" ? 1 : 0;
      const lim: [number, number] = pl ? (cmp.show === "significance" ? [-5, 5] : limitsFor([pl], [0.5, 99.5], true, center)) : [-1, 1];
      return (
        <div className="relative flex h-full flex-col gap-2">
          <SkyCanvas {...commonCanvas} className="min-h-0 flex-1" plane={pl} limits={lim} diverging
            display={{ ...display, stretch: display.stretch === "asinh" ? "asinh" : "linear" }}
            markers={markersAt(A)} loading={cmp.loading || (!!cmp.data && !pl)}
            caption={A && B ? `${mode === "ratio" ? "A / B" : "A − B"}${cmp.show === "significance" ? " (σ)" : ""} · ${formatDate(A.observation_time)} vs ${formatDate(B.observation_time)}` : undefined}
            badge={cmp.data ? `Δt ${formatDuration(Number(cmp.data.stats.delta_t_days ?? 0))}` : undefined}
            emptyText={cmp.error ?? (playing ? "Pause playback to compute the comparison." : "Choose frames A and B.")} />
          {cmp.data && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
              <span className="text-slate-300">{cmp.data.display}</span>
              <span>A: {cmp.data.a.observation_ids.length} frame(s) · B: {cmp.data.b.observation_ids.length} frame(s)</span>
              {cmp.data.stats.significance_robust_std != null && <span>robust σ of significance: {Number(cmp.data.stats.significance_robust_std).toFixed(2)}</span>}
              <span>limits {lim[0].toPrecision(3)} … {lim[1].toPrecision(3)}{cmp.show === "significance" ? " σ" : mode === "difference" ? " MJy/sr" : ""}</span>
              <a className="text-sky-300 hover:underline" href={apiUrl(cmp.data.fits_url)} download>FITS</a>
            </div>
          )}
          {cmp.data?.caveats?.length ? <details className="text-[11px] text-amber-200/90"><summary className="cursor-pointer">Caveats ({cmp.data.caveats.length})</summary><ul className="list-disc pl-5">{cmp.data.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul></details> : null}
        </div>
      );
    }
    // stacked
    return (
      <div className="flex h-full flex-col gap-2">
        <SkyCanvas {...commonCanvas} className="min-h-0 flex-1" plane={stackPlane} limits={stackPlane ? limitsFor([stackPlane], display.percentiles) : [0, 1]}
          markers={markersAt(undefined, stack?.mjd_mean)} loading={stacks.loading || (!!stack && !stackPlane)}
          caption={stack ? `${stack.method} stack · ${stack.n_frames} frames · ${formatDate(stack.time_start)} → ${formatDate(stack.time_end)} · ${stack.wavelength_mean.toFixed(3)} µm` : undefined}
          badge={stacks.data ? `${stackIdx + 1} / ${stacks.data.length}` : undefined} emptyText={stacks.error ?? "No stacks"} />
        {stacks.data && (
          <div className="flex flex-wrap items-center gap-1">
            <button type="button" className="rounded bg-slate-800 px-2 py-1 text-xs hover:bg-slate-700" onClick={() => setStackIdx((i) => Math.max(0, i - 1))}>◀</button>
            {stacks.data.map((s, i) => (
              <button key={s.key} type="button" title={s.key} onClick={() => setStackIdx(i)}
                className={`rounded px-2 py-1 text-[11px] ${i === stackIdx ? "bg-amber-500 font-semibold text-slate-950" : "bg-slate-800 text-slate-300 hover:bg-slate-700"}`}>
                {s.group_by === "channel" ? `${s.wavelength_mean.toFixed(2)} µm` : formatDate(s.time_start)}
              </button>
            ))}
            <button type="button" className="rounded bg-slate-800 px-2 py-1 text-xs hover:bg-slate-700" onClick={() => setStackIdx((i) => Math.min((stacks.data?.length ?? 1) - 1, i + 1))}>▶</button>
          </div>
        )}
        {stack?.warnings?.length ? <Note tone="warn">{stack.warnings.join(" ")}</Note> : null}
      </div>
    );
  })();

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked */
    }
  };

  return (
    <div className={`grid grid-rows-[auto_1fr_auto] bg-slate-950 text-slate-100 ${embedded ? "h-full overflow-y-auto lg:overflow-hidden" : "min-h-screen lg:h-screen"}`}>
      {/* Top bar */}
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900 px-3 pt-2 pb-4">
        {embedded ? (
          <button type="button" onClick={onClose} className="flex items-center gap-2 rounded border border-teal-500/40 px-2 py-1 text-xs text-teal-100 hover:border-teal-300" title="Back to the 3D Atlas (Esc)">
            <span aria-hidden>←</span> Atlas
          </button>
        ) : (
          <Link to="/" className="flex items-center gap-2 pr-2" aria-label="SPHEREx Atlas">
            <img src="/favicon.svg" alt="" className="h-7 w-7" />
          </Link>
        )}
        <span className="text-sm font-bold tracking-wide">SPHEREx <span className="text-amber-400">Explorer</span></span>
        <Link to="/about" className="text-xs text-slate-400 hover:text-slate-200" title="What SPHEREx is, how to read the viewer, glossary">Help</Link>
        <SearchBar initial={params} busy={loadingSeries} onSubmit={openSearch} />
        {availability && (
          <span className="rounded bg-emerald-900/50 px-2 py-0.5 text-[11px] text-emerald-200" role="status"
            title={`${availability.n} SPHEREx frames in ${availability.visits} visits, ${formatDate(availability.first)} to ${formatDate(availability.last)} (${availability.releases})`}>
            {availability.n} observations · {availability.visits} visits · {formatDate(availability.first)} → {formatDate(availability.last)}
          </span>
        )}
        {grid && (
          <div className="flex flex-wrap items-center gap-3 font-mono text-[11px] text-slate-400">
            <span title="Field centre">{formatRaHms(grid.center_ra)} {formatDecDms(grid.center_dec)}</span>
            <span><Term k="fov">FOV</Term> {formatAngle(grid.fov_deg)}</span>
            <span title="Grid pixel scale and current zoom">{grid.pixel_scale_arcsec.toFixed(2)}″/px · {zoomText}</span>
            <button type="button" onClick={() => setView(null)} className="rounded bg-slate-800 px-2 py-1 font-sans text-xs text-slate-200 hover:bg-slate-700" title="Reset zoom and pan (0)">Reset view</button>
            <button type="button" onClick={copyLink} className="rounded bg-slate-800 px-2 py-1 font-sans text-xs text-slate-200 hover:bg-slate-700">{copied ? "Copied" : "Copy link"}</button>
          </div>
        )}
        {api.usingMocks && <span className="rounded bg-fuchsia-700 px-2 py-0.5 text-[11px] font-semibold">MOCK DATA</span>}
      </header>

      <div className="grid min-h-0 grid-cols-1 md:grid-cols-[17rem_1fr] lg:grid-cols-[17rem_1fr_19rem]">
        {/* Left sidebar */}
        <aside className="order-2 min-h-0 border-slate-800 bg-slate-900/60 md:order-none md:overflow-y-auto md:border-r" aria-label="Viewer controls">
          <ModePanel mode={mode} onMode={setMode} frameA={A} frameB={B} bIsAuto={!pinnedB && compareChannel === null}
            onSetBToCurrent={() => A && setPinnedB(A.observation_id)} onAutoB={() => { setPinnedB(null); setCompareChannel(null); }}
            blinkMs={blinkMs} onBlinkMs={setBlinkMs} compare={compareOpts} onCompare={setCompareOpts} stack={stackOpts} onStack={setStackOpts} />
          <ChannelSelector channels={channels} counts={channelCounts} value={filter} onChange={setFilter}
            compareChannel={compareChannel} onCompareChannel={setCompareChannel} showCompare={mode === "side-by-side" || mode === "blink"}
            nVisible={visible.length} nTotal={frames.length} />
          <DisplayControls value={display} onChange={setDisplay} limits={limitsOf(A, planeA)} units={A?.units} diverging={isCmp} showPlane={!isCmp} />
          <OverlayControls value={overlays} onChange={setOverlays} st={{
            hasTarget: frames.some((f) => f.predicted) || !!series.processed?.query?.target,
            gaia: overlays.gaia ? { count: gaia.data?.count ?? 0, loading: gaia.loading, error: gaia.error } : undefined,
            catwise: overlays.catwise ? { count: catwise.data?.count ?? 0, loading: catwise.loading, error: catwise.error } : undefined,
            sources: { count: analysis.data?.sources.length ?? 0, loading: analysis.loading, error: analysis.error },
          }} />
          <p className="px-3 py-3 text-[11px] leading-relaxed text-slate-500">
            Keys: <kbd>Space</kbd> play · <kbd>←</kbd>/<kbd>→</kbd> step · <kbd>B</kbd> pin B · <kbd>+</kbd>/<kbd>−</kbd> zoom · <kbd>0</kbd> reset · drag to pan, wheel to zoom.
          </p>
        </aside>

        {/* Centre */}
        <main className="order-1 flex min-h-[70vh] min-w-0 flex-col gap-2 p-2 md:order-none lg:min-h-0">
          <div className="min-h-[50vh] flex-1 lg:min-h-0">{viewer}</div>
          {frames.length > 0 && mode !== "stacked" && (
            <PlaybackBar frames={visible} index={Math.max(0, vIndex)} onIndex={goto} playing={playing} onPlaying={setPlaying}
              fps={fps} onFps={setFps} timing={timing} onTiming={setTiming} loop={loop} onLoop={setLoop} axis={axis} onAxis={setAxis}
              reference={B ? visible.findIndex((f) => f.seriesIndex === B.seriesIndex) : null}
              isLoaded={(i) => planes.has(visible[i]?.seriesIndex)} loadedCount={visible.filter((f) => planes.has(f.seriesIndex)).length} />
          )}
        </main>

        {/* Right sidebar */}
        <aside className="order-3 min-h-0 border-slate-800 bg-slate-900/60 md:col-span-2 md:border-t lg:col-span-1 lg:overflow-y-auto lg:border-t-0 lg:border-l" aria-label="Observation details">
          <ReadoutPanel r={hover} units={isCmp ? (cmp.show === "significance" ? "σ" : mode === "ratio" ? "" : "MJy/sr") : A?.units} />
          <ObservationPanel f={A} label={mode === "single" ? "Observation" : "Observation A"} />
          {(mode === "blink" || mode === "side-by-side" || isCmp) && <ObservationPanel f={B} label="Observation B" />}
          <TargetPanel q={series.processed?.query ?? null} frame={A} />
          {frames.length > 0 && (
            <AnalysisPanel data={analysis.data} loading={analysis.loading} error={analysis.error} selected={selectedSource}
              onSelect={setSelectedSource} productsLoading={sourceProducts.loading} />
          )}
          <QualityPanel frames={frames} errors={series.processed?.errors ?? []} skipped={series.processed?.skipped.length ?? 0} />
          <AladinContext grid={grid} target={A?.predicted ?? null} />
        </aside>
      </div>

      {frames.length > 0 && (
        <PlotsPanel frames={frames} planes={planes} currentSeriesIndex={cur} visible={visibleSet}
          onSelect={(i) => { setCur(i); if (!visibleSet.has(i)) setFilter(ALL_CHANNELS); }}
          center={grid ? [grid.center_ra, grid.center_dec] : null}
          lightCurves={plotCurves} spectra={plotSpectra} />
      )}
    </div>
  );
}

function Welcome({ error, embedded }: { error: string | null; embedded?: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      {error && <Note tone="error">{error}</Note>}
      <p className="max-w-md text-sm text-slate-400">
        Enter a target name or coordinates above{embedded ? ", or close this panel, pick a star in the Atlas and press “Open SPHEREx images”" : ""}.
        The <Link className="text-amber-300 underline" to="/targets">challenge targets</Link> are good places to start.
      </p>
    </div>
  );
}

/** Display-limit group of a frame: its wavelength channel ("channel" mode) or its detector. */
function limitGroup(f: Frame, mode: string): number {
  return mode === "channel" && f.channel ? 1000 + f.channel.index : f.detector;
}
