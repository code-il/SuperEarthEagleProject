/**
 * Spectral channel selector: SPHEREx's 102 nominal channels (6 detectors x 17).
 *
 * This chooses WHICH WAVELENGTHS take part; the playback bar chooses WHEN.
 * Keeping one channel gives a true same-wavelength time-lapse; mixing channels
 * mixes the source's spectrum into the brightness changes.
 */
import type { Channel } from "../types";
import { DETECTOR_COLORS } from "./Timeline";
import { Panel, Row } from "./ui";

export interface ChannelFilter {
  /** null = all channels. */
  channels: number[] | null;
  wlMin: number | null;
  wlMax: number | null;
}

export const ALL_CHANNELS: ChannelFilter = { channels: null, wlMin: null, wlMax: null };

export function channelFilterMatches(f: ChannelFilter, chIndex: number | undefined, wavelength: number): boolean {
  if (f.channels && (chIndex === undefined || !f.channels.includes(chIndex))) return false;
  if (f.wlMin !== null && wavelength < f.wlMin) return false;
  if (f.wlMax !== null && wavelength > f.wlMax) return false;
  return true;
}

export interface ChannelSelectorProps {
  channels: Channel[];
  /** Frames per channel index in the loaded series. */
  counts: Map<number, number>;
  value: ChannelFilter;
  onChange: (f: ChannelFilter) => void;
  /** Channel shown in pane B for "compare channels" (side-by-side / blink). */
  compareChannel: number | null;
  onCompareChannel: (c: number | null) => void;
  showCompare: boolean;
  nVisible: number;
  nTotal: number;
}

export function ChannelSelector(p: ChannelSelectorProps) {
  const { channels, counts, value } = p;
  const byDet = [1, 2, 3, 4, 5, 6].map((d) => channels.filter((c) => c.detector === d));
  const present = channels.filter((c) => counts.get(c.index));
  const sel = new Set(value.channels ?? []);

  const toggle = (c: Channel, e: React.MouseEvent) => {
    if (!counts.get(c.index)) return;
    let next: number[];
    if (e.shiftKey && value.channels?.length) {
      const last = value.channels[value.channels.length - 1];
      const [a, b] = [Math.min(last, c.index), Math.max(last, c.index)];
      next = [...new Set([...value.channels, ...present.filter((x) => x.index >= a && x.index <= b).map((x) => x.index)])];
    } else if (e.ctrlKey || e.metaKey) {
      next = sel.has(c.index) ? [...sel].filter((x) => x !== c.index) : [...sel, c.index];
    } else {
      next = sel.size === 1 && sel.has(c.index) ? [] : [c.index];
    }
    p.onChange({ ...value, channels: next.length ? next : null });
  };

  return (
    <Panel title="Spectral channels" actions={
      <button type="button" className="text-[11px] text-slate-400 hover:text-amber-300" onClick={() => p.onChange({ channels: null, wlMin: null, wlMax: null })}>All</button>
    }>
      <p className="text-[11px] leading-snug text-slate-400">
        Click a channel to play only that wavelength (Ctrl-click to add, Shift-click for a range). Brighter cells have more frames.
      </p>
      <div className="space-y-1" role="group" aria-label="Spectral channels by detector">
        {byDet.map((row, d) => (
          <div key={d} className="flex items-center gap-1">
            <span className="w-5 text-[10px] font-semibold" style={{ color: DETECTOR_COLORS[d] }}>D{d + 1}</span>
            <div className="grid flex-1 grid-cols-17 gap-px">
              {row.map((c) => {
                const n = counts.get(c.index) ?? 0;
                const on = sel.has(c.index);
                const cmp = p.compareChannel === c.index;
                return (
                  <button key={c.index} type="button" disabled={!n}
                    aria-pressed={on} aria-label={`${c.label}: ${n} frames`}
                    title={`${c.label} · ${c.wavelength_min.toFixed(3)}–${c.wavelength_max.toFixed(3)} µm · ${n} frame${n === 1 ? "" : "s"}`}
                    onClick={(e) => toggle(c, e)}
                    className={`h-4 rounded-[2px] ${on ? "ring-2 ring-amber-400" : ""} ${cmp ? "outline-2 outline-sky-400" : ""} ${n ? "cursor-pointer" : "cursor-default"}`}
                    style={{ background: n ? DETECTOR_COLORS[d] : "#1e293b", opacity: n ? Math.min(1, 0.35 + n / 8) : 1 }} />
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[10px] text-slate-500"><span>0.75 µm</span><span>5.0 µm</span></div>
      <Row label="Wavelength (µm)">
        <input aria-label="Minimum wavelength" type="number" step="0.01" min="0.7" max="5.1" placeholder="min" className="w-16 rounded-lg border border-white/10 bg-white/[0.06] px-1 py-0.5 text-xs"
          value={value.wlMin ?? ""} onChange={(e) => p.onChange({ ...value, wlMin: e.target.value === "" ? null : Number(e.target.value) })} />
        –
        <input aria-label="Maximum wavelength" type="number" step="0.01" min="0.7" max="5.1" placeholder="max" className="w-16 rounded-lg border border-white/10 bg-white/[0.06] px-1 py-0.5 text-xs"
          value={value.wlMax ?? ""} onChange={(e) => p.onChange({ ...value, wlMax: e.target.value === "" ? null : Number(e.target.value) })} />
      </Row>
      <p className="text-xs text-slate-300">
        Showing <b>{p.nVisible}</b> of {p.nTotal} frames
        {value.channels && <> in {value.channels.length} channel{value.channels.length > 1 ? "s" : ""}</>}
      </p>
      {p.showCompare && (
        <Row label="Compare channel (B)" hint="Pane B shows the frame in this channel closest in time to A">
          <select aria-label="Compare channel" className="max-w-40 rounded-lg border border-white/10 bg-white/[0.06] px-1 py-1 text-xs"
            value={p.compareChannel ?? ""} onChange={(e) => p.onCompareChannel(e.target.value === "" ? null : Number(e.target.value))}>
            <option value="">same as A (time)</option>
            {present.map((c) => <option key={c.index} value={c.index}>{c.label}</option>)}
          </select>
        </Row>
      )}
    </Panel>
  );
}
