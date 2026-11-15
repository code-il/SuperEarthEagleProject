/**
 * Time-lapse controls: play/pause, step, first/last, speed, timing, loop,
 * jump-to-date, and the real-time timeline.
 */
import { useState } from "react";
import type { Frame } from "../state/useSeries";
import { formatDateTime, formatDuration, isoToMjd, nearestIndex } from "../lib/time";
import { Timeline, type TimeAxis } from "./Timeline";

export type Timing = "uniform" | "proportional";

export interface PlaybackBarProps {
  frames: Frame[];
  index: number;
  onIndex: (i: number) => void;
  playing: boolean;
  onPlaying: (p: boolean) => void;
  fps: number;
  onFps: (f: number) => void;
  timing: Timing;
  onTiming: (t: Timing) => void;
  loop: boolean;
  onLoop: (l: boolean) => void;
  axis: TimeAxis;
  onAxis: (a: TimeAxis) => void;
  reference?: number | null;
  isLoaded: (i: number) => boolean;
  loadedCount: number;
}

const SPEEDS = [0.5, 1, 2, 3, 5, 8, 12];

function Btn(props: React.ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  const { label, children, className, ...rest } = props;
  return (
    <button type="button" aria-label={label} title={label}
      className={`flex h-8 min-w-8 items-center justify-center rounded border border-white/10 bg-white/[0.06] px-2 text-sm text-slate-100 hover:bg-white/10 disabled:opacity-40 ${className ?? ""}`}
      {...rest}>
      {children}
    </button>
  );
}

export function PlaybackBar(p: PlaybackBarProps) {
  const { frames, index } = p;
  const n = frames.length;
  const f = frames[index];
  const [jump, setJump] = useState("");
  const mjds = frames.map((x) => x.mjd);
  const prev = index > 0 ? frames[index - 1] : null;
  const epochs = [...new Set(frames.map((x) => x.epoch))];

  const doJump = () => {
    const t = jump.trim();
    if (!t) return;
    const m = /^\d+(\.\d+)?$/.test(t) && Number(t) > 40000 ? Number(t) : isoToMjd(t);
    if (Number.isFinite(m)) p.onIndex(nearestIndex(mjds, m));
  };

  return (
<<<<<<< HEAD
    <section aria-label="Time-lapse playback" className="glass rounded-[20px] px-4 py-2.5">
=======
    <section aria-label="Time-lapse playback" className="rounded-md border border-white/[0.06] bg-slate-900/80 px-3 py-2">
>>>>>>> 15015553c6e704fff68626c1145fa538697a9c25
      <div className="flex flex-wrap items-center gap-2">
        <Btn label="First frame" onClick={() => p.onIndex(0)} disabled={!n}>⏮</Btn>
        <Btn label="Previous frame (←)" onClick={() => p.onIndex(Math.max(0, index - 1))} disabled={!n || index === 0}>◀</Btn>
        <Btn label={p.playing ? "Pause (space)" : "Play (space)"} onClick={() => p.onPlaying(!p.playing)} disabled={n < 2}
          className={`w-16 font-semibold ${p.playing ? "border-amber-500 text-amber-300" : ""}`}>
          {p.playing ? "❚❚ Pause" : "▶ Play"}
        </Btn>
        <Btn label="Next frame (→)" onClick={() => p.onIndex(Math.min(n - 1, index + 1))} disabled={!n || index >= n - 1}>▶</Btn>
        <Btn label="Last frame" onClick={() => p.onIndex(n - 1)} disabled={!n}>⏭</Btn>

        <label className="ml-2 flex items-center gap-1 text-xs text-slate-300">
          Speed
          <select className="rounded-lg border border-white/10 bg-white/[0.06] px-1 py-1 text-xs" value={p.fps} onChange={(e) => p.onFps(Number(e.target.value))}>
            {SPEEDS.map((s) => <option key={s} value={s}>{s} fps</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1 text-xs text-slate-300" title="Uniform: equal time per frame. Proportional: frames separated by longer real gaps stay on screen longer.">
          Timing
          <select className="rounded-lg border border-white/10 bg-white/[0.06] px-1 py-1 text-xs" value={p.timing} onChange={(e) => p.onTiming(e.target.value as Timing)}>
            <option value="uniform">equal per frame</option>
            <option value="proportional">follows real gaps</option>
          </select>
        </label>
        <label className="flex items-center gap-1 text-xs text-slate-300">
          <input type="checkbox" checked={p.loop} onChange={(e) => p.onLoop(e.target.checked)} /> Loop
        </label>
        <label className="flex items-center gap-1 text-xs text-slate-300">
          Axis
          <select className="rounded-lg border border-white/10 bg-white/[0.06] px-1 py-1 text-xs" value={p.axis} onChange={(e) => p.onAxis(e.target.value as TimeAxis)}>
            <option value="visits">by visit (gaps labelled)</option>
            <option value="linear">true time (linear)</option>
          </select>
        </label>
        <form className="ml-auto flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); doJump(); }}>
          <input aria-label="Jump to date or MJD" placeholder="Jump: 2026-02-14 or MJD"
            className="w-44 rounded-lg border border-white/10 bg-white/[0.06] px-2 py-1 text-xs placeholder:text-slate-500"
            value={jump} onChange={(e) => setJump(e.target.value)} />
          <Btn label="Jump to nearest frame" type="submit">Go</Btn>
        </form>
      </div>

      <Timeline mjds={mjds} detectors={frames.map((x) => x.detector)} current={index} reference={p.reference}
        loaded={p.isLoaded} axis={p.axis} onSelect={p.onIndex} />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-300">
        <input type="range" min={0} max={Math.max(0, n - 1)} value={index} aria-label="Frame index"
          onChange={(e) => p.onIndex(Number(e.target.value))} className="w-40" />
        <span className="font-mono text-amber-300">{f ? formatDateTime(f.observation_time) : "—"}</span>
        <span>Frame <b>{n ? index + 1 : 0}</b> of <b>{n}</b></span>
        <span>Visit {f ? epochs.indexOf(f.epoch) + 1 : 0} of {epochs.length}</span>
        {prev && f && <span title="Time since the previous frame">Δt prev: {formatDuration(f.mjd - prev.mjd)}</span>}
        {f && n > 0 && <span title="Time since the first frame">since first: {formatDuration(f.mjd - frames[0].mjd)}</span>}
        <span className="text-slate-500">span {n ? formatDuration(frames[n - 1].mjd - frames[0].mjd) : "—"}</span>
        {p.loadedCount < n && <span className="text-slate-500">images loaded {p.loadedCount}/{n}</span>}
        <div className="ml-auto flex gap-1">
          {epochs.map((e, k) => (
            <button key={e} type="button" className={`rounded px-2 py-0.5 text-[11px] ${f?.epoch === e ? "bg-amber-500/20 text-amber-300" : "bg-white/[0.06] text-slate-300 hover:bg-white/10"}`}
              onClick={() => p.onIndex(frames.findIndex((x) => x.epoch === e))} title="Jump to the first frame of this visit">
              Visit {k + 1}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
