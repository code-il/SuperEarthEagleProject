/**
 * Coordinate / name search with validation and field-of-view control.
 * Accepts: "269.452 4.693", "17h57m48.5s +04d41m36s", "17:57:48.5 +04:41:36",
 * "17 57 48.5 +04 41 36", or a name ("Barnard's Star", "WISE 0855", "M42").
 */
import { useEffect, useState } from "react";
import type { ExploreParams } from "../lib/api";
import { CoordinateInputError, FOV_MAX, FOV_MIN, formatDecDms, formatRaHms, parseQuery, validateFov } from "../lib/coords";
import { MANDATORY_TARGETS } from "../lib/targets";
import { DETECTOR_COLORS } from "./Timeline";

export interface SearchBarProps {
  initial: ExploreParams | null;
  onSubmit: (p: ExploreParams) => void;
  busy?: boolean;
}

function initialText(p: ExploreParams | null): string {
  if (!p) return "";
  if (p.q) return p.q;
  return p.ra !== undefined && p.dec !== undefined ? `${p.ra} ${p.dec}` : "";
}

const FOV_UNITS = { deg: 1, arcmin: 1 / 60, arcsec: 1 / 3600 } as const;
type FovUnit = keyof typeof FOV_UNITS;

export function SearchBar({ initial, onSubmit, busy }: SearchBarProps) {
  const [text, setText] = useState(initialText(initial));
  const [fovVal, setFovVal] = useState(String(+((initial?.fov ?? 0.05) * 60).toFixed(3)));
  const [fovUnit, setFovUnit] = useState<FovUnit>("arcmin");
  const [detectors, setDetectors] = useState<number[]>(initial?.detectors ?? [6]);
  const [release, setRelease] = useState(initial?.release?.join(",") ?? "");
  const [showAdv, setShowAdv] = useState(false);
  const [from, setFrom] = useState(initial?.date_start ?? "");
  const [to, setTo] = useState(initial?.date_end ?? "");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(initialText(initial));
    if (initial) {
      setFovVal(String(+(initial.fov / FOV_UNITS[fovUnit]).toFixed(4)));
      setDetectors(initial.detectors);
      setRelease(initial.release?.join(",") ?? "");
      setFrom(initial.date_start ?? "");
      setTo(initial.date_end ?? "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);

  let hint: string | null = null;
  try {
    const q = text.trim() ? parseQuery(text) : null;
    if (q?.kind === "coordinates") hint = `${formatRaHms(q.ra)} ${formatDecDms(q.dec)} (${q.ra.toFixed(5)}°, ${q.dec.toFixed(5)}°)`;
    else if (q?.kind === "name") hint = "name: resolved by CDS Sesame (proper motion included)";
  } catch (e) {
    hint = null;
    if (!(e instanceof CoordinateInputError)) throw e;
  }

  const submit = (override?: string) => {
    setError(null);
    try {
      const q = parseQuery(override ?? text);
      const fov = validateFov(Number(fovVal) * FOV_UNITS[fovUnit]);
      const p: ExploreParams = { fov, detectors };
      if (q.kind === "coordinates") {
        p.ra = q.ra;
        p.dec = q.dec;
      } else p.q = q.name;
      if (release) p.release = release.split(",");
      if (from) p.date_start = from;
      if (to) p.date_end = to;
      onSubmit(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const toggleDet = (d: number) => setDetectors((ds) => (ds.includes(d) ? ds.filter((x) => x !== d) : [...ds, d].sort()));

  return (
    <form className="flex min-w-0 flex-1 basis-full flex-wrap items-center gap-2 md:basis-auto" onSubmit={(e) => { e.preventDefault(); submit(); }} aria-label="Search the sky">
      <div className="relative min-w-0 basis-full sm:min-w-64 sm:flex-1 sm:basis-auto">
        <input
          aria-label="Target name or coordinates" aria-invalid={!!error} aria-describedby="search-hint"
          className={`w-full rounded border bg-white/[0.03] px-3 py-1.5 text-sm text-slate-100 placeholder:text-slate-500 ${error ? "border-red-500" : "border-white/10 focus:border-amber-400"}`}
          placeholder="Name or coordinates: Barnard's Star · 269.452 4.693 · 17h57m48.5s +04d41m36s"
          value={text} onChange={(e) => { setText(e.target.value); setError(null); }}
          list="mandatory-targets"
        />
        <datalist id="mandatory-targets">{MANDATORY_TARGETS.map((t) => <option key={t.key} value={t.query}>{t.label}</option>)}</datalist>
        <p id="search-hint" className={`absolute left-1 top-full mt-0.5 truncate text-[10px] ${error ? "text-red-400" : "text-slate-500"}`} role={error ? "alert" : undefined}>
          {error ?? hint ?? ""}
        </p>
      </div>
      <label className="flex items-center gap-1 text-xs text-slate-300" title={`Field of view: ${FOV_MIN}°–${FOV_MAX}°. Bigger fields mean more download and processing time.`}>
        FOV
        <input aria-label="Field of view" type="number" min="0" step="any" value={fovVal} onChange={(e) => setFovVal(e.target.value)}
          className="w-16 rounded-lg border border-white/10 bg-white/[0.03] px-1.5 py-1.5 text-xs" />
        <select aria-label="Field of view unit" value={fovUnit} className="rounded-lg border border-white/10 bg-white/[0.03] px-1 py-1.5 text-xs"
          onChange={(e) => {
            const u = e.target.value as FovUnit;
            setFovVal(String(+((Number(fovVal) * FOV_UNITS[fovUnit]) / FOV_UNITS[u]).toFixed(4)));
            setFovUnit(u);
          }}>
          <option value="deg">deg</option><option value="arcmin">arcmin</option><option value="arcsec">arcsec</option>
        </select>
      </label>
      <div className="flex items-center gap-0.5" role="group" aria-label="Detectors to load" title="Detectors to load (D1 0.75 µm … D6 5.0 µm). Each extra detector adds downloads.">
        {[1, 2, 3, 4, 5, 6].map((d) => (
          <button key={d} type="button" aria-pressed={detectors.includes(d)} onClick={() => toggleDet(d)}
            className={`h-7 w-7 rounded text-[11px] font-semibold ${detectors.includes(d) ? "text-slate-950" : "bg-white/[0.06] text-slate-400"}`}
            style={detectors.includes(d) ? { background: DETECTOR_COLORS[d - 1] } : undefined}>D{d}</button>
        ))}
      </div>
      <button type="button" className="text-xs text-slate-400 hover:text-slate-200" onClick={() => setShowAdv(!showAdv)} aria-expanded={showAdv}>More ▾</button>
      <button type="submit" disabled={busy || !detectors.length}
        className="btn btn-primary px-4 py-1.5 text-sm">
        {busy ? "Loading…" : "Explore"}
      </button>
      {showAdv && (
        <div className="flex w-full flex-wrap items-center gap-3 pt-3 text-xs text-slate-300">
          <label className="flex items-center gap-1">Release
            <select value={release} onChange={(e) => setRelease(e.target.value)} className="rounded-lg border border-white/10 bg-white/[0.03] px-1 py-1">
              <option value="">default (DR1 + QR3)</option><option value="DR1">DR1</option><option value="QR3">QR3</option><option value="all">all incl. deep</option>
            </select>
          </label>
          <label className="flex items-center gap-1">From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded-lg border border-white/10 bg-white/[0.03] px-1 py-0.5" /></label>
          <label className="flex items-center gap-1">To <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="rounded-lg border border-white/10 bg-white/[0.03] px-1 py-0.5" /></label>
          <span className="text-slate-500">Challenge targets:</span>
          {MANDATORY_TARGETS.map((t) => (
            <button key={t.key} type="button" title={t.why} className="rounded-lg bg-white/[0.06] px-2 py-0.5 hover:bg-white/10"
              onClick={() => { setText(t.query); submit(t.query); }}>{t.label}</button>
          ))}
        </div>
      )}
    </form>
  );
}
