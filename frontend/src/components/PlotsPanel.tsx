/**
 * Bottom panel: coverage (time x wavelength), light curve and spectrum.
 *
 * Photometry belongs to the analysis workstream (Person 4). When it supplies
 * LightCurve / Spectrum data they are plotted as-is. Until then the panel shows a
 * clearly labelled QUICK-LOOK value: the mean surface brightness in a 3x3-pixel box
 * at the target's predicted position. It is not calibrated photometry.
 */
import { lazy, Suspense, useMemo, useState } from "react";
import type { ImagePlane, LightCurve, Spectrum } from "../types";
import type { Frame } from "../state/useSeries";
import { makeTan, world2pix } from "../lib/wcs";
import { DETECTOR_COLORS } from "./Timeline";
import { Spinner } from "./SkyCanvas";

const Plot = lazy(async () => {
  const [{ default: Plotly }, { default: factory }] = await Promise.all([import("plotly.js-dist-min"), import("react-plotly.js/factory")]);
  return { default: factory(Plotly) };
});

type Tab = "coverage" | "lightcurve" | "spectrum";

export interface PlotsPanelProps {
  frames: Frame[];
  /** Planes by seriesIndex. */
  planes: Map<number, ImagePlane>;
  currentSeriesIndex: number | null;
  visible: Set<number>;
  onSelect: (seriesIndex: number) => void;
  center: [number, number] | null;
  lightCurves: LightCurve[];
  spectra: Spectrum[];
}

/** Mean of valid pixels in a (2r+1)^2 box around (ra, dec). */
export function boxMean(plane: ImagePlane, ra: number, dec: number, r = 1): number | null {
  let p: [number, number] | null = null;
  try {
    p = world2pix(makeTan(plane.wcs), ra, dec);
  } catch {
    return null;
  }
  if (!p) return null;
  const x0 = Math.round(p[0]), y0 = Math.round(p[1]);
  let s = 0, n = 0;
  for (let y = y0 - r; y <= y0 + r; y++)
    for (let x = x0 - r; x <= x0 + r; x++) {
      if (x < 0 || y < 0 || x >= plane.width || y >= plane.height) continue;
      const i = y * plane.width + x;
      const v = plane.data[i];
      if (Number.isFinite(v) && !plane.mask?.[i]) {
        s += v;
        n++;
      }
    }
  return n >= Math.ceil(((2 * r + 1) ** 2) / 2) ? s / n : null;
}

const DARK = {
  autosize: true, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(15,23,42,0.6)",
  font: { color: "#cbd5e1", size: 11 }, margin: { l: 56, r: 12, t: 26, b: 40 },
  xaxis: { gridcolor: "#1e293b", zerolinecolor: "#334155" }, yaxis: { gridcolor: "#1e293b", zerolinecolor: "#334155" },
  legend: { orientation: "h", y: 1.18, font: { size: 10 } }, hovermode: "closest",
};
const CONFIG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };

export function PlotsPanel(p: PlotsPanelProps) {
  const [tab, setTab] = useState<Tab>("coverage");
  const [open, setOpen] = useState(true);
  const quick = useMemo(() => {
    return p.frames.map((f) => {
      const pl = p.planes.get(f.seriesIndex);
      const pos = f.predicted ?? p.center;
      return pl && pos ? boxMean(pl, pos[0], pos[1]) : null;
    });
  }, [p.frames, p.planes, p.center]);

  const cur = p.currentSeriesIndex != null ? p.frames[p.currentSeriesIndex] : undefined;
  const dets = [...new Set(p.frames.map((f) => f.detector))].sort();

  const coverage = useMemo(() => dets.map((d) => {
    const fs = p.frames.filter((f) => f.detector === d);
    return {
      type: "scattergl", mode: "markers", name: `D${d}`,
      x: fs.map((f) => f.observation_time), y: fs.map((f) => f.wavelength), customdata: fs.map((f) => f.seriesIndex),
      marker: { color: DETECTOR_COLORS[d - 1], size: 7, opacity: fs.map((f) => (p.visible.has(f.seriesIndex) ? 0.95 : 0.2)), line: { width: 0 } },
      hovertemplate: "%{x}<br>%{y:.3f} µm<extra>D" + d + "</extra>",
    };
  }), [p.frames, p.visible, dets]);

  const hasP4LC = p.lightCurves.length > 0, hasP4Sp = p.spectra.length > 0;

  const lcData = useMemo(() => {
    if (hasP4LC) return p.lightCurves.map((lc) => ({
      type: "scatter", mode: "markers", name: lc.label ?? lc.source_id,
      x: lc.points.map((q) => new Date((q.mjd - 40587) * 86400_000).toISOString()), y: lc.points.map((q) => q.flux),
      error_y: { type: "data", array: lc.points.map((q) => q.flux_err ?? 0), visible: true, thickness: 1, width: 0 },
      marker: { size: 6, color: lc.points.map((q) => q.wavelength ?? 0), colorscale: "Viridis", showscale: true, colorbar: { title: { text: "µm" }, thickness: 8 } },
      customdata: lc.points.map((q) => p.frames.find((f) => f.observation_id === q.observation_id)?.seriesIndex ?? null),
    }));
    const idx = p.frames.map((_, i) => i).filter((i) => quick[i] != null && p.visible.has(i));
    return [{
      type: "scatter", mode: "markers", name: "quick-look 3×3 px mean",
      x: idx.map((i) => p.frames[i].observation_time), y: idx.map((i) => quick[i]), customdata: idx,
      marker: { size: 6, color: idx.map((i) => p.frames[i].wavelength), colorscale: "Viridis", showscale: true, colorbar: { title: { text: "µm" }, thickness: 8 } },
      hovertemplate: "%{x}<br>%{y:.3g} MJy/sr<br>λ %{marker.color:.3f} µm<extra></extra>",
    }];
  }, [hasP4LC, p.lightCurves, p.frames, quick, p.visible]);

  const spData = useMemo(() => {
    if (hasP4Sp) return p.spectra.map((s) => ({
      type: "scatter", mode: "lines+markers", name: s.label ?? s.source_id,
      x: s.points.map((q) => q.wavelength), y: s.points.map((q) => q.flux),
      error_y: { type: "data", array: s.points.map((q) => q.flux_err ?? 0), visible: true, thickness: 1, width: 0 },
    }));
    const epochs = [...new Set(p.frames.map((f) => f.epoch))];
    return epochs.map((e, k) => {
      const idx = p.frames.map((_, i) => i).filter((i) => p.frames[i].epoch === e && quick[i] != null && p.visible.has(i));
      return {
        type: "scatter", mode: "markers", name: `visit ${k + 1} (${p.frames[idx[0] ?? 0]?.observation_time.slice(0, 10) ?? ""})`,
        x: idx.map((i) => p.frames[i].wavelength), y: idx.map((i) => quick[i]), customdata: idx, marker: { size: 6 },
        hovertemplate: "%{x:.3f} µm<br>%{y:.3g} MJy/sr<extra></extra>",
      };
    });
  }, [hasP4Sp, p.spectra, p.frames, quick, p.visible]);

  const nowLine = cur ? [{ type: "line", xref: "x", yref: "paper", x0: cur.observation_time, x1: cur.observation_time, y0: 0, y1: 1, line: { color: "#fbbf24", width: 1.5, dash: "dot" } }] : [];
  const wlLine = cur ? [{ type: "line", xref: "x", yref: "paper", x0: cur.wavelength, x1: cur.wavelength, y0: 0, y1: 1, line: { color: "#fbbf24", width: 1.5, dash: "dot" } }] : [];

  const onClick = (e: { points: { customdata?: unknown }[] }) => {
    const v = e.points?.[0]?.customdata;
    if (typeof v === "number") p.onSelect(v);
  };

  const withValue = quick.filter((x) => x != null).length;
  const nLoaded = p.frames.filter((f) => p.planes.has(f.seriesIndex)).length;
  const quickStatus = nLoaded < p.frames.length
    ? `${withValue} values · ${nLoaded}/${p.frames.length} frames loaded`
    : withValue === 0
      ? "no usable pixels at the target in any frame (masked, e.g. saturated): pick a shorter-wavelength detector"
      : `${withValue}/${p.frames.length} frames have usable pixels at the target`;

  return (
    <section aria-label="Plots" className="flex min-h-0 flex-col border-t border-white/[0.06] bg-slate-950">
      <div className="flex items-center gap-1 px-3 pt-1.5">
        {(["coverage", "lightcurve", "spectrum"] as Tab[]).map((t) => (
          <button key={t} type="button" onClick={() => { setTab(t); setOpen(true); }}
            className={`rounded-t px-3 py-1 text-xs ${tab === t && open ? "bg-white/[0.06] font-semibold text-amber-300" : "text-slate-400 hover:text-slate-200"}`}>
            {t === "coverage" ? "Coverage" : t === "lightcurve" ? "Light curve" : "Spectrum"}
          </button>
        ))}
        <span className="ml-3 text-[11px] text-slate-500">
          {tab === "coverage" && "Each dot is one frame: when it was taken and at what wavelength. Click a dot to open it."}
          {tab !== "coverage" && !(tab === "lightcurve" ? hasP4LC : hasP4Sp) && (
            <span className="text-amber-300/80">Quick-look only: mean MJy/sr in 3×3 px at the predicted target position. Not calibrated photometry; frames mix wavelengths. ({quickStatus})</span>
          )}
          {tab !== "coverage" && (tab === "lightcurve" ? hasP4LC : hasP4Sp) && "Photometry from the analysis workstream."}
        </span>
        <button type="button" className="ml-auto text-xs text-slate-400 hover:text-slate-200" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide ▾" : "Show ▴"}</button>
      </div>
      {open && (
        <div className="h-52 overflow-hidden px-2 pb-1">
          <Suspense fallback={<div className="flex h-full items-center justify-center"><Spinner label="Loading plots…" /></div>}>
            {tab === "coverage" && (
              <Plot data={coverage} onClick={onClick} useResizeHandler style={{ width: "100%", height: "100%" }} config={CONFIG}
                layout={{ ...DARK, shapes: nowLine, xaxis: { ...DARK.xaxis, title: { text: "Observation time (UTC)" }, type: "date" }, yaxis: { ...DARK.yaxis, title: { text: "λ (µm)" } } }} />
            )}
            {tab === "lightcurve" && (
              <Plot data={lcData} onClick={onClick} useResizeHandler style={{ width: "100%", height: "100%" }} config={CONFIG}
                layout={{ ...DARK, showlegend: false, shapes: nowLine, xaxis: { ...DARK.xaxis, title: { text: "Observation time (UTC)" }, type: "date" },
                  yaxis: { ...DARK.yaxis, title: { text: hasP4LC ? p.lightCurves[0].units : "surface brightness (MJy/sr)" } } }} />
            )}
            {tab === "spectrum" && (
              <Plot data={spData} onClick={onClick} useResizeHandler style={{ width: "100%", height: "100%" }} config={CONFIG}
                layout={{ ...DARK, shapes: wlLine, xaxis: { ...DARK.xaxis, title: { text: "Wavelength (µm)" } },
                  yaxis: { ...DARK.yaxis, title: { text: hasP4Sp ? p.spectra[0].units : "surface brightness (MJy/sr)" } } }} />
            )}
          </Suspense>
        </div>
      )}
    </section>
  );
}
