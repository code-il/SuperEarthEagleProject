/** Right sidebar: current observation, cursor readout, target, data quality. */
import type { PixelReadout, QueryPosition } from "../types";
import type { Frame } from "../state/useSeries";
import { apiUrl } from "../lib/api";
import { formatDecDms, formatRaHms } from "../lib/coords";
import { formatDateTime } from "../lib/time";
import { totalPm } from "../lib/catalog";
import { KV, Note, Panel, Term } from "./ui";

// Flags that matter most for interpreting a pixel at the target.
const SERIOUS = new Set(["NONFUNC", "MISSING_DATA", "HOT", "COLD", "OVERFLOW", "NONLINEAR", "SUR_ERROR", "PERSIST", "OUTLIER"]);

export function ReadoutPanel({ r, units }: { r: PixelReadout | null; units?: string }) {
  return (
    <Panel title="Cursor">
      <dl className="space-y-0.5">
        <KV k="RA" v={r ? `${formatRaHms(r.ra)}  ${r.ra.toFixed(5)}°` : "—"} />
        <KV k="Dec" v={r ? `${formatDecDms(r.dec)}  ${r.dec.toFixed(5)}°` : "—"} />
        <KV k="Pixel (x, y)" v={r ? `${r.x + 1}, ${r.y + 1}` : "—"} />
        <KV k="Value" v={r ? (r.masked ? "masked" : r.value === null ? "no data" : `${r.value.toPrecision(4)} ${units ?? ""}`) : "—"} />
      </dl>
    </Panel>
  );
}

export function ObservationPanel({ f, label = "Observation" }: { f?: Frame; label?: string }) {
  if (!f) return <Panel title={label}><p className="text-xs text-slate-500">No observation selected.</p></Panel>;
  const serious = f.quality_flags.filter((x) => SERIOUS.has(x));
  return (
    <Panel title={label}>
      <dl className="space-y-0.5">
        <KV k="ID" v={f.observation_id} />
        <KV k="Time" v={formatDateTime(f.observation_time)} />
        <KV k="MJD" v={f.mjd.toFixed(5)} />
        {/* The filter's wavelength changes across the frame (~0.01 µm over 3′): this is the value at the field centre. */}
        <KV k={<Term k="wavelength">λ at centre</Term>} v={`${f.wavelength.toFixed(3)} µm`} />
        {f.bandwidth != null && <KV k="Bandwidth" v={`${f.bandwidth.toFixed(3)} µm`} />}
        <KV k="λ calibration" v={f.wavelength_source?.startsWith("CWAVE") ? "CWAVE/CBAND" : f.wavelength_source ? "WCS-WAVE (approx.)" : "—"} />
        <KV k={<Term k="channel">Channel</Term>} v={f.channel?.label ?? "—"} />
        <KV k={<Term k="detector">Detector</Term>} v={`D${f.detector}`} />
        <KV k={<Term k="release">Release</Term>} v={f.data_release} />
        <KV k={<Term k="psf">PSF FWHM</Term>} v={f.psf_fwhm_arcsec != null ? `${f.psf_fwhm_arcsec.toFixed(1)}″` : "—"} />
        <KV k="Valid pixels" v={`${(f.valid_fraction * 100).toFixed(1)}%`} />
        <KV k={<Term k="zodi">Zodi (median)</Term>} v={f.zodi_median != null ? `${f.zodi_median.toFixed(3)} ${f.units}` : "—"} />
        <KV k="Residual bkg" v={f.background_level != null ? `${f.background_level.toFixed(3)} ${f.units}` : "—"} />
      </dl>
      <div>
        <p className="mb-1 text-[11px] text-slate-400">Flags at target</p>
        <div className="flex flex-wrap gap-1">
          {f.quality_flags.length ? f.quality_flags.map((q) => (
            <span key={q} className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${SERIOUS.has(q) ? "bg-amber-900/60 text-amber-200" : "bg-white/[0.06] text-slate-300"}`}>{q}</span>
          )) : <span className="text-xs text-emerald-400">none</span>}
        </div>
      </div>
      {serious.length > 0 && <Note tone="warn">The target pixel carries {serious.join(", ")}. Brightness at the target in this frame may be unreliable.</Note>}
      <p className="text-[11px] leading-snug text-slate-500" title={f.history.join("\n")}>{f.processing_method}</p>
      <div className="flex gap-3 text-xs">
        <a className="text-sky-300 hover:underline" href={apiUrl(f.fits_url)} download>Processed FITS</a>
        {f.image_url && <a className="text-sky-300 hover:underline" href={apiUrl(f.image_url)} target="_blank" rel="noreferrer">PNG</a>}
      </div>
    </Panel>
  );
}

export function TargetPanel({ q, frame }: { q: QueryPosition | null; frame?: Frame }) {
  if (!q) return null;
  const t = q.target;
  const pm = t ? totalPm(t) : null;
  return (
    <Panel title="Target">
      <dl className="space-y-0.5">
        <KV k="Query" v={q.query} mono={false} />
        {q.name && <KV k="Resolved" v={q.name.replace(/^NAME\s+/, "")} mono={false} />}
        <KV k="Field centre" v={`${formatRaHms(q.ra)} ${formatDecDms(q.dec)}`} />
        {q.epoch && <KV k="Centre epoch" v={q.epoch.slice(0, 10)} />}
        {t?.object_type && <KV k="Type" v={t.object_type} />}
        {pm !== null && <KV k={<Term k="proper_motion">Proper motion</Term>} v={`${(pm / 1000).toFixed(2)}″/yr`} />}
        {t?.parallax_mas != null && <KV k="Parallax" v={`${t.parallax_mas.toFixed(1)} mas`} />}
        {frame?.predicted && <KV k="Predicted here" v={`${formatRaHms(frame.predicted[0])} ${formatDecDms(frame.predicted[1])}`} />}
      </dl>
      {t?.source && <p className="text-[10px] text-slate-500">Resolved by {t.source}</p>}
    </Panel>
  );
}

export function QualityPanel({ frames, errors, skipped }: { frames: Frame[]; errors: { error: string; observation_id?: string }[]; skipped: number }) {
  const counts = new Map<string, number>();
  for (const f of frames) for (const q of f.quality_flags) counts.set(q, (counts.get(q) ?? 0) + 1);
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const lowValid = frames.filter((f) => f.valid_fraction < 0.8).length;
  return (
    <Panel title="Data quality (series)" defaultOpen={false}>
      <p className="text-xs text-slate-300">{frames.length} frames · {skipped} skipped (target outside image) · {errors.length} failed</p>
      {lowValid > 0 && <p className="text-xs text-amber-300">{lowValid} frames have under 80% valid pixels</p>}
      <div className="space-y-0.5">
        {sorted.map(([k, n]) => (
          <div key={k} className="flex items-center gap-2 text-[11px]">
            <span className={`w-24 font-mono ${SERIOUS.has(k) ? "text-amber-200" : "text-slate-300"}`}>{k}</span>
            <div className="h-1.5 flex-1 rounded-lg bg-white/[0.06]"><div className="h-1.5 rounded-lg bg-slate-500" style={{ width: `${(100 * n) / Math.max(1, frames.length)}%` }} /></div>
            <span className="w-8 text-right text-slate-400">{n}</span>
          </div>
        ))}
        {!sorted.length && <p className="text-xs text-emerald-400">No flags at the target in any frame.</p>}
      </div>
      {errors.length > 0 && (
        <details className="text-[11px] text-red-300">
          <summary className="cursor-pointer">Failed downloads / processing</summary>
          <ul className="mt-1 list-disc pl-4">{errors.slice(0, 20).map((e, i) => <li key={i}>{e.observation_id ?? ""} {e.error}</li>)}</ul>
        </details>
      )}
    </Panel>
  );
}
