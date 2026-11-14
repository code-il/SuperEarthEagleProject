import type { ViewMode } from "../types";
import type { Frame } from "../state/useSeries";
import { formatDateTime } from "../lib/time";
import { Note, Panel, Row, Seg, Select, Slider, Toggle } from "./ui";

export type Scope = "frame" | "visit";
export type ShowPlane = "image" | "significance";

export interface CompareOptions {
  scopeA: Scope;
  scopeB: Scope;
  psfMatching: boolean;
  matchBackground: boolean;
  matchWavelength: boolean;
  smoothFwhm: number;
  show: ShowPlane;
}

export interface StackOptions {
  groupBy: "epoch" | "date" | "channel" | "epoch_channel";
  method: "median" | "mean" | "weighted";
}

export const DEFAULT_COMPARE: CompareOptions = { scopeA: "frame", scopeB: "frame", psfMatching: true, matchBackground: true, matchWavelength: false, smoothFwhm: 0, show: "image" };
export const DEFAULT_STACK: StackOptions = { groupBy: "epoch", method: "median" };

export const MODES: { value: ViewMode; label: string; title: string }[] = [
  { value: "single", label: "Single", title: "One observation at a time" },
  { value: "blink", label: "Blink", title: "Flip between frame A and reference B" },
  { value: "side-by-side", label: "Side by side", title: "A and B next to each other, zoom and pan locked" },
  { value: "difference", label: "Difference", title: "A − B computed by the processing engine (PSF and background matched)" },
  { value: "ratio", label: "Ratio", title: "A / B computed by the processing engine" },
  { value: "stacked", label: "Stacked", title: "Frames combined per visit / day / channel by the processing engine" },
];

export interface ModePanelProps {
  mode: ViewMode;
  onMode: (m: ViewMode) => void;
  frameA?: Frame;
  frameB?: Frame;
  bIsAuto: boolean;
  onSetBToCurrent: () => void;
  onAutoB: () => void;
  blinkMs: number;
  onBlinkMs: (ms: number) => void;
  compare: CompareOptions;
  onCompare: (c: CompareOptions) => void;
  stack: StackOptions;
  onStack: (s: StackOptions) => void;
}

export function ModePanel(p: ModePanelProps) {
  const needsB = p.mode === "blink" || p.mode === "side-by-side" || p.mode === "difference" || p.mode === "ratio";
  const c = p.compare;
  const setC = <K extends keyof CompareOptions>(k: K, v: CompareOptions[K]) => p.onCompare({ ...c, [k]: v });
  return (
    <Panel title="Comparison mode">
      <Seg label="Viewing mode" value={p.mode} onChange={p.onMode} options={MODES} />
      {needsB && (
        <div className="space-y-1 rounded border border-slate-800 bg-slate-900 p-2">
          <div className="flex items-center gap-2 text-xs">
            <span className="rounded bg-amber-500 px-1.5 font-bold text-slate-950">A</span>
            <span className="truncate text-slate-200">{p.frameA ? formatDateTime(p.frameA.observation_time) : "—"}</span>
            <span className="ml-auto text-slate-500">current frame</span>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="rounded bg-sky-400 px-1.5 font-bold text-slate-950">B</span>
            <span className="truncate text-slate-200">{p.frameB ? formatDateTime(p.frameB.observation_time) : "—"}</span>
            <span className="ml-auto text-slate-500">{p.bIsAuto ? "auto" : "pinned"}</span>
          </div>
          {p.frameA && p.frameB && (
            <p className="font-mono text-[11px] text-slate-400">
              λ {p.frameA.wavelength.toFixed(3)} vs {p.frameB.wavelength.toFixed(3)} µm
              {p.frameA.detector !== p.frameB.detector && <span className="text-amber-300"> · different detectors</span>}
            </p>
          )}
          <div className="flex gap-1">
            <button type="button" className="flex-1 rounded bg-slate-800 px-2 py-1 text-xs hover:bg-slate-700" onClick={p.onSetBToCurrent}
              title="Pin the frame on screen as B, then move A with the playback controls">Pin current as B</button>
            <button type="button" className="flex-1 rounded bg-slate-800 px-2 py-1 text-xs hover:bg-slate-700 disabled:opacity-40" disabled={p.bIsAuto} onClick={p.onAutoB}
              title="B = frame from another visit at the closest wavelength">Auto B</button>
          </div>
        </div>
      )}
      {p.mode === "blink" && (
        <Slider label="Blink interval" min={150} max={2000} step={50} value={p.blinkMs} onChange={p.onBlinkMs} format={(v) => `${(v / 1000).toFixed(2)}s`} />
      )}
      {(p.mode === "difference" || p.mode === "ratio") && (
        <div className="space-y-2">
          <Row label="A uses">
            <Select<Scope> label="A scope" value={c.scopeA} onChange={(v) => setC("scopeA", v)} options={[{ value: "frame", label: "this frame" }, { value: "visit", label: "whole visit (stack)" }]} />
          </Row>
          <Row label="B uses">
            <Select<Scope> label="B scope" value={c.scopeB} onChange={(v) => setC("scopeB", v)} options={[{ value: "frame", label: "this frame" }, { value: "visit", label: "whole visit (stack)" }]} />
          </Row>
          <Toggle label="PSF matching" checked={c.psfMatching} onChange={(v) => setC("psfMatching", v)} hint="Blur the sharper image to the broader PSF before comparing" />
          <Toggle label="Match background" checked={c.matchBackground} onChange={(v) => setC("matchBackground", v)} />
          <Toggle label="Match wavelengths (visit stacks)" checked={c.matchWavelength} onChange={(v) => setC("matchWavelength", v)} hint="Keep only A/B frames observed at matching wavelengths" />
          <Slider label="Extra smoothing" min={0} max={30} step={1} value={c.smoothFwhm} onChange={(v) => setC("smoothFwhm", v)} format={(v) => (v ? `${v}″` : "off")} />
          <Row label="Show">
            <Select<ShowPlane> label="Show plane" value={c.show} onChange={(v) => setC("show", v)} options={[{ value: "image", label: p.mode === "ratio" ? "A / B" : "A − B (MJy/sr)" }, { value: "significance", label: "significance (σ)" }]} />
          </Row>
          <Note>
            SPHEREx PSFs are undersampled, so even stationary stars leave residuals in single-frame differences. ~15″ smoothing or visit stacks reduce them.
          </Note>
        </div>
      )}
      {p.mode === "stacked" && (
        <div className="space-y-2">
          <Row label="Group by">
            <Select label="Group by" value={p.stack.groupBy} onChange={(v) => p.onStack({ ...p.stack, groupBy: v })}
              options={[{ value: "epoch", label: "visit" }, { value: "date", label: "day" }, { value: "channel", label: "channel" }, { value: "epoch_channel", label: "visit × channel" }]} />
          </Row>
          <Row label="Combine">
            <Select label="Stack method" value={p.stack.method} onChange={(v) => p.onStack({ ...p.stack, method: v })}
              options={[{ value: "median", label: "median" }, { value: "mean", label: "mean" }, { value: "weighted", label: "inverse-variance" }]} />
          </Row>
        </div>
      )}
    </Panel>
  );
}
