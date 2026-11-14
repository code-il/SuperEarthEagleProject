/**
 * The main image viewer: one processed SPHEREx plane on the common north-up grid.
 *
 * - Zoom (wheel, +/-), pan (drag), reset (double-click or 0); pixels stay sharp.
 * - Hover readout: pixel, RA/Dec, value in physical units (from the FITS, not the PNG).
 * - Overlays in sky coordinates (target, Gaia, CatWISE, Person-4 sources), compass, scale bar.
 * The science array is only read, never changed.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DisplaySettings, ImagePlane, Marker, PixelReadout, ViewState } from "../types";
import { renderPlane } from "../lib/render";
import { makeTan, northAngle, pix2world, pixelScaleArcsec, world2pix } from "../lib/wcs";
import { formatDecDms, formatRaHms } from "../lib/coords";

export interface SkyCanvasProps {
  plane: ImagePlane | null;
  limits: [number, number];
  display: DisplaySettings;
  diverging?: boolean;
  markers?: Marker[];
  showLabels?: boolean;
  showCompass?: boolean;
  view: ViewState | null;
  onViewChange: (v: ViewState | null) => void;
  onHover?: (r: PixelReadout | null) => void;
  /** Top-left caption (date, wavelength…). */
  caption?: string;
  /** Top-right caption (frame counter…). */
  badge?: string;
  loading?: boolean;
  /** Shown when there is no plane. */
  emptyText?: string;
  /** PNG to show while the FITS loads or if it cannot be parsed. */
  fallbackUrl?: string | null;
  className?: string;
}

const MARKER_STYLE: Record<Marker["kind"], string> = {
  target: "#fbbf24",
  gaia: "#38bdf8",
  catwise: "#e879f9",
  source: "#4ade80",
};

/** Analysis markers by SourceMarker.kind: candidates stand out, artefacts recede. */
const TONE_STYLE: Record<string, string> = {
  candidate: "#fb923c",
  known: "#4ade80",
  artifact: "#94a3b8",
  source: "#2dd4bf",
};

function markerColor(m: Marker): string {
  if (m.selected) return "#fbbf24";
  return m.kind === "source" && m.tone ? (TONE_STYLE[m.tone] ?? MARKER_STYLE.source) : MARKER_STYLE[m.kind];
}

export function fitZoom(w: number, h: number, W: number, H: number): number {
  return Math.max(0.05, Math.min(W / w, H / h) * 0.94);
}

export function SkyCanvas(props: SkyCanvasProps) {
  const { plane, limits, display, diverging, markers = [], view, onViewChange, onHover } = props;
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ W: 600, H: 500 });
  const [hoverMarker, setHoverMarker] = useState<{ m: Marker; sx: number; sy: number } | null>(null);
  const drag = useRef<{ x: number; y: number; cx: number; cy: number; moved: boolean } | null>(null);

  // Track container size.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ W: Math.max(50, e.contentRect.width), H: Math.max(50, e.contentRect.height) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const tan = useMemo(() => {
    try {
      return plane ? makeTan(plane.wcs) : null;
    } catch {
      return null;
    }
  }, [plane]);

  // Render science values -> RGBA once per plane/limits/display change.
  const bitmap = useMemo(() => {
    if (!plane) return null;
    const c = document.createElement("canvas");
    c.width = plane.width;
    c.height = plane.height;
    c.getContext("2d")!.putImageData(renderPlane(plane, limits, display, diverging), 0, 0);
    return c;
  }, [plane, limits, display, diverging]);

  const v: ViewState | null = useMemo(() => {
    if (!plane) return null;
    return view ?? { zoom: fitZoom(plane.width, plane.height, size.W, size.H), cx: (plane.width - 1) / 2, cy: (plane.height - 1) / 2 };
  }, [plane, view, size]);

  const toScreen = useCallback((x: number, y: number): [number, number] => {
    if (!v) return [0, 0];
    return [size.W / 2 + (x - v.cx) * v.zoom, size.H / 2 - (y - v.cy) * v.zoom];
  }, [v, size]);

  const toImage = useCallback((sx: number, sy: number): [number, number] => {
    if (!v) return [0, 0];
    return [v.cx + (sx - size.W / 2) / v.zoom, v.cy - (sy - size.H / 2) / v.zoom];
  }, [v, size]);

  const screenMarkers = useMemo(() => {
    if (!tan || !plane) return [];
    const out: { m: Marker; sx: number; sy: number }[] = [];
    for (const m of markers) {
      const p = world2pix(tan, m.ra, m.dec);
      if (!p || p[0] < -2 || p[1] < -2 || p[0] > plane.width + 1 || p[1] > plane.height + 1) continue;
      const [sx, sy] = toScreen(p[0], p[1]);
      out.push({ m, sx, sy });
    }
    return out;
  }, [markers, tan, plane, toScreen]);

  // Draw.
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(size.W * dpr);
    c.height = Math.round(size.H * dpr);
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.W, size.H);
    if (!plane || !bitmap || !v) return;
    const [left, top] = toScreen(-0.5, plane.height - 0.5);
    // Masked / no-coverage pixels are transparent: show them as a hatched dark area.
    g.save();
    g.fillStyle = "#1c1a2b";
    g.fillRect(left, top, plane.width * v.zoom, plane.height * v.zoom);
    g.imageSmoothingEnabled = false;
    g.drawImage(bitmap, left, top, plane.width * v.zoom, plane.height * v.zoom);
    g.strokeStyle = "rgba(148,163,184,0.35)";
    g.lineWidth = 1;
    g.strokeRect(left, top, plane.width * v.zoom, plane.height * v.zoom);
    g.restore();

    // Markers.
    const pxArc = tan ? pixelScaleArcsec(tan) : 6.15;
    for (const { m, sx, sy } of screenMarkers) {
      g.save();
      g.strokeStyle = markerColor(m);
      g.fillStyle = markerColor(m);
      g.lineWidth = m.kind === "target" ? 2 : 1.25;
      g.shadowColor = "rgba(0,0,0,0.8)";
      g.shadowBlur = 2;
      if (m.kind === "target") {
        const r = Math.max(8, (12 / pxArc) * v.zoom);
        const gap = r * 0.45;
        g.beginPath();
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          g.moveTo(sx + dx * gap, sy + dy * gap);
          g.lineTo(sx + dx * (r + 6), sy + dy * (r + 6));
        }
        g.stroke();
        g.beginPath();
        g.arc(sx, sy, r, 0, Math.PI * 2);
        g.setLineDash([3, 3]);
        g.stroke();
      } else if (m.kind === "gaia") {
        const r = Math.max(3, Math.min(14, (21 - (m.mag ?? 18)) * 1.4)) * Math.min(1.6, Math.max(0.6, v.zoom / 12));
        g.beginPath();
        g.arc(sx, sy, r, 0, Math.PI * 2);
        g.stroke();
      } else if (m.kind === "catwise") {
        const r = Math.max(3, Math.min(12, (17 - (m.mag ?? 15)) * 1.6)) * Math.min(1.6, Math.max(0.6, v.zoom / 12));
        if (m.suspect) {
          g.setLineDash([2, 3]);
          g.globalAlpha = 0.6;
        }
        g.strokeRect(sx - r, sy - r, 2 * r, 2 * r);
      } else {
        const r = Math.max(7, v.zoom * 1.2);
        if (m.suspect) {
          g.setLineDash([2, 3]);
          g.globalAlpha = 0.6;
        }
        g.beginPath();
        g.moveTo(sx, sy - r);
        g.lineTo(sx + r, sy);
        g.lineTo(sx, sy + r);
        g.lineTo(sx - r, sy);
        g.closePath();
        g.stroke();
        if (m.selected) {
          g.setLineDash([]);
          g.globalAlpha = 1;
          g.lineWidth = 2;
          g.beginPath();
          g.arc(sx, sy, r + 6, 0, Math.PI * 2);
          g.stroke();
        }
      }
      if (props.showLabels && m.label && (m.kind === "target" || m.kind === "source")) {
        g.font = "600 12px ui-sans-serif, system-ui";
        g.fillText(m.label, sx + 12, sy - 12);
      }
      g.restore();
    }

    // Compass and scale bar.
    if (props.showCompass && tan) {
      const ang = northAngle(tan);
      const ox = size.W - 46, oy = size.H - 46, L = 26;
      const arrow = (a: number, label: string) => {
        const ex = ox - Math.sin(a) * L, ey = oy - Math.cos(a) * L;
        g.beginPath();
        g.moveTo(ox, oy);
        g.lineTo(ex, ey);
        g.stroke();
        g.fillText(label, ox - Math.sin(a) * (L + 10) - 4, oy - Math.cos(a) * (L + 10) + 4);
      };
      g.save();
      g.strokeStyle = g.fillStyle = "rgba(226,232,240,0.9)";
      g.lineWidth = 1.5;
      g.font = "600 11px ui-sans-serif, system-ui";
      arrow(ang, "N");
      arrow(ang + Math.PI / 2, "E");
      // Scale bar: pick a round angular length near 80 screen px.
      const arcPerScreenPx = pxArc / v.zoom;
      const target = arcPerScreenPx * 80;
      const nice = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600].find((x) => x >= target * 0.6) ?? 3600;
      const len = nice / arcPerScreenPx;
      const bx = 16, by = size.H - 18;
      g.beginPath();
      g.moveTo(bx, by);
      g.lineTo(bx + len, by);
      g.moveTo(bx, by - 4);
      g.lineTo(bx, by + 4);
      g.moveTo(bx + len, by - 4);
      g.lineTo(bx + len, by + 4);
      g.stroke();
      g.fillText(nice >= 60 ? `${nice / 60}′` : `${nice}″`, bx + len / 2 - 8, by - 7);
      g.restore();
    }
  }, [plane, bitmap, v, size, screenMarkers, toScreen, tan, props.showCompass, props.showLabels]);

  // Interaction.
  const setView = (nv: ViewState) => onViewChange(nv);

  const onWheel = (e: React.WheelEvent) => {
    if (!v || !plane) return;
    const rect = canvasRef.current!.getBoundingClientRect();
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    const [ix, iy] = toImage(sx, sy);
    const z = Math.min(200, Math.max(0.2, v.zoom * Math.exp(-e.deltaY * 0.0015)));
    // keep the point under the cursor fixed
    setView({ zoom: z, cx: ix - (sx - size.W / 2) / z, cy: iy + (sy - size.H / 2) / z });
  };

  useEffect(() => {
    // React's onWheel is passive; block page scroll over the canvas.
    const el = canvasRef.current;
    if (!el) return;
    const stop = (e: WheelEvent) => e.preventDefault();
    el.addEventListener("wheel", stop, { passive: false });
    return () => el.removeEventListener("wheel", stop);
  }, []);

  const readout = (sx: number, sy: number): PixelReadout | null => {
    if (!plane || !tan) return null;
    const [ix, iy] = toImage(sx, sy);
    const x = Math.round(ix), y = Math.round(iy);
    const inside = x >= 0 && y >= 0 && x < plane.width && y < plane.height;
    const [ra, dec] = pix2world(tan, ix, iy);
    const idx = y * plane.width + x;
    const val = inside ? plane.data[idx] : NaN;
    return { x, y, ra, dec, value: inside && Number.isFinite(val) ? val : null, masked: inside && !!plane.mask?.[idx] };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    if (drag.current && v) {
      const dx = sx - drag.current.x, dy = sy - drag.current.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) drag.current.moved = true;
      setView({ zoom: v.zoom, cx: drag.current.cx - dx / v.zoom, cy: drag.current.cy + dy / v.zoom });
    }
    onHover?.(readout(sx, sy));
    let best: { m: Marker; sx: number; sy: number } | null = null;
    let bd = 10;
    for (const s of screenMarkers) {
      const d = Math.hypot(s.sx - sx, s.sy - sy);
      if (d < bd) {
        bd = d;
        best = s;
      }
    }
    setHoverMarker(best);
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (!v) return;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    const rect = canvasRef.current!.getBoundingClientRect();
    drag.current = { x: e.clientX - rect.left, y: e.clientY - rect.top, cx: v.cx, cy: v.cy, moved: false };
  };

  return (
    <div ref={wrapRef} className={`relative overflow-hidden rounded-md bg-[#0a0d18] select-none ${props.className ?? ""}`}
      role="img" aria-label={props.caption ? `SPHEREx image: ${props.caption}` : "SPHEREx image viewer"}>
      {!plane && props.fallbackUrl && (
        <img src={props.fallbackUrl} alt="" className="absolute inset-0 m-auto max-h-full max-w-full object-contain [image-rendering:pixelated] opacity-80" />
      )}
      <canvas
        ref={canvasRef}
        className="absolute inset-0 h-full w-full cursor-crosshair touch-none"
        style={{ width: size.W, height: size.H }}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={() => (drag.current = null)}
        onPointerLeave={() => {
          drag.current = null;
          onHover?.(null);
          setHoverMarker(null);
        }}
        onDoubleClick={() => onViewChange(null)}
      />
      {props.caption && (
        <div className="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-1 font-mono text-xs text-slate-100">{props.caption}</div>
      )}
      {props.badge && (
        <div className="pointer-events-none absolute right-2 top-2 rounded bg-black/60 px-2 py-1 font-mono text-xs text-slate-200">{props.badge}</div>
      )}
      {hoverMarker && (
        <div className="pointer-events-none absolute z-10 max-w-64 rounded border border-slate-600 bg-slate-900/95 px-2 py-1 text-xs text-slate-100 shadow"
          style={{ left: Math.min(hoverMarker.sx + 12, size.W - 220), top: hoverMarker.sy + 12 }}>
          <div className="font-semibold" style={{ color: markerColor(hoverMarker.m) }}>{hoverMarker.m.label ?? hoverMarker.m.id}</div>
          {hoverMarker.m.title && <div className="text-slate-300">{hoverMarker.m.title}</div>}
          <div className="font-mono text-slate-400">{formatRaHms(hoverMarker.m.ra)} {formatDecDms(hoverMarker.m.dec)}</div>
        </div>
      )}
      {(props.loading || (!plane && !props.fallbackUrl)) && (
        <div className="absolute inset-0 flex items-center justify-center">
          {props.loading ? <Spinner /> : <p className="max-w-sm px-6 text-center text-sm text-slate-400">{props.emptyText ?? "No image"}</p>}
        </div>
      )}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex flex-col items-center gap-2" role="status" aria-live="polite">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-600 border-t-amber-400" />
      <span className="text-xs text-slate-400">{label ?? "Loading…"}</span>
    </div>
  );
}
