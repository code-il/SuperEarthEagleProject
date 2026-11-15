/**
 * Timeline of observations positioned by their real timestamps.
 *
 * SPHEREx revisits a field in short bursts (minutes to days) roughly every six months.
 * "By visit" gives each visit the same width (true time inside it) and labels the gaps
 * between visits with their real length. "True time" is a linear time axis.
 * Neither assumes frames are equally spaced.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { formatDate, formatDuration, groupByGap } from "../lib/time";

export type TimeAxis = "visits" | "linear";

export interface TimelineProps {
  mjds: number[];
  /** Detector per frame (tick colour). */
  detectors?: number[];
  current: number;
  /** Reference frame (blink/compare "B"), if any. */
  reference?: number | null;
  /** Frames whose image has loaded (others drawn faded). */
  loaded?: (i: number) => boolean;
  axis: TimeAxis;
  onSelect: (i: number) => void;
  gapDays?: number;
}

export const DETECTOR_COLORS = ["#60a5fa", "#34d399", "#a3e635", "#facc15", "#fb923c", "#f87171"];

export function timelinePositions(mjds: number[], axis: TimeAxis, gapDays = 20): { x: number[]; groups: { start: number; end: number; x0: number; x1: number }[] } {
  const n = mjds.length;
  if (!n) return { x: [], groups: [] };
  const groups = groupByGap(mjds, gapDays);
  if (axis === "linear" || groups.length === 1) {
    const t0 = mjds[0], t1 = mjds[n - 1];
    const span = t1 - t0 || 1;
    const x = mjds.map((m) => (n === 1 ? 0.5 : (m - t0) / span));
    return { x, groups: groups.map((g) => ({ ...g, x0: x[g.start], x1: x[g.end] })) };
  }
  const gapFrac = 0.06;
  const w = (1 - gapFrac * (groups.length - 1)) / groups.length;
  const x = new Array<number>(n);
  const out = groups.map((g, k) => {
    const x0 = k * (w + gapFrac);
    const t0 = mjds[g.start], span = mjds[g.end] - t0;
    for (let i = g.start; i <= g.end; i++) x[i] = x0 + (span > 0 ? ((mjds[i] - t0) / span) * w : w / 2);
    return { ...g, x0, x1: x0 + w };
  });
  return { x, groups: out };
}

export function Timeline({ mjds, detectors, current, reference, loaded, axis, onSelect, gapDays = 20 }: TimelineProps) {
  const ref = useRef<SVGSVGElement>(null);
  const [W, setW] = useState(800);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(Math.max(100, el.getBoundingClientRect().width));
    const ro = new ResizeObserver(([e]) => setW(Math.max(100, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
    // Re-attach when the svg (re)mounts: it is not rendered until there are frames.
  }, [mjds.length > 0]);
  const { x, groups } = useMemo(() => timelinePositions(mjds, axis, gapDays), [mjds, axis, gapDays]);
  const PAD = 12;

  const pick = (clientX: number) => {
    const el = ref.current;
    if (!el || !x.length) return;
    const r = el.getBoundingClientRect();
    const f = (clientX - r.left - PAD) / (r.width - 2 * PAD);
    let best = 0, bd = Infinity;
    x.forEach((xi, i) => {
      const d = Math.abs(xi - f);
      if (d < bd) {
        bd = d;
        best = i;
      }
    });
    onSelect(best);
  };

  const pos = (f: number) => PAD + (W - 2 * PAD) * f;

  return (
    <div className="relative h-16 w-full overflow-hidden">
      <svg
        ref={ref}
        className="absolute inset-0 h-full w-full cursor-pointer"
        role="slider"
        aria-label="Observation timeline"
        aria-valuemin={1}
        aria-valuemax={mjds.length}
        aria-valuenow={current + 1}
        aria-valuetext={mjds[current] !== undefined ? `Frame ${current + 1} of ${mjds.length}` : undefined}
        tabIndex={0}
        onPointerDown={(e) => {
          (e.target as Element).setPointerCapture?.(e.pointerId);
          pick(e.clientX);
        }}
        onPointerMove={(e) => e.buttons === 1 && pick(e.clientX)}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") onSelect(Math.min(mjds.length - 1, current + 1));
          else if (e.key === "ArrowLeft") onSelect(Math.max(0, current - 1));
          else return;
          e.preventDefault();
          e.stopPropagation();
        }}
      >
        {groups.map((g, k) => (
          <rect key={k} x={pos(g.x0) - 1} y={18} width={Math.max(3, (W - 2 * PAD) * (g.x1 - g.x0))} height={22} rx={3} className="fill-slate-800" />
        ))}
        {x.map((xi, i) => (
          <line key={i} x1={pos(xi)} x2={pos(xi)} y1={20} y2={38}
            stroke={DETECTOR_COLORS[((detectors?.[i] ?? 6) - 1) % 6]} strokeWidth={1.5}
            opacity={loaded && !loaded(i) ? 0.25 : 0.9} />
        ))}
        {reference != null && x[reference] !== undefined && (
          <g>
            <line x1={pos(x[reference])} x2={pos(x[reference])} y1={14} y2={44} stroke="#38bdf8" strokeWidth={2} strokeDasharray="3 2" />
            <text x={pos(x[reference])} y={12} textAnchor="middle" className="fill-sky-300 text-[10px] font-semibold">B</text>
          </g>
        )}
        {x[current] !== undefined && (
          <g>
            <line x1={pos(x[current])} x2={pos(x[current])} y1={12} y2={46} stroke="#fbbf24" strokeWidth={3} />
            <circle cx={pos(x[current])} cy={12} r={4} fill="#fbbf24" />
          </g>
        )}
      </svg>
      {/* Visit labels and real gap lengths */}
      {groups.map((g, k) => (
        <div key={k} className="pointer-events-none absolute bottom-0 text-[10px] text-slate-400"
          style={{ left: pos((g.x0 + g.x1) / 2), transform: "translateX(-50%)", whiteSpace: "nowrap" }}>
          {formatDate(new Date((mjds[g.start] - 40587) * 86400_000))}
          {g.end > g.start && <span className="text-slate-500"> · {formatDuration(mjds[g.end] - mjds[g.start])}</span>}
        </div>
      ))}
      {axis === "visits" && groups.slice(1).map((g, k) => (
        <div key={`gap${k}`} className="pointer-events-none absolute top-0 text-[10px] font-medium text-slate-500"
          style={{ left: pos((groups[k].x1 + g.x0) / 2), transform: "translateX(-50%)", whiteSpace: "nowrap" }}>
          +{formatDuration(mjds[g.start] - mjds[groups[k].end])}
        </div>
      ))}
    </div>
  );
}
