/**
 * Analysis panel (Person 4 results): sources found in the field, their class,
 * confidence, measured motion, catalogue match and the criteria behind the class.
 * Selecting a source highlights it on the image and loads its light curve and
 * spectrum into the bottom panel.
 */
import { useMemo, useState } from "react";
import type { AnalysisResponse, SourceSummary } from "../types";
import { formatDecDms, formatRaHms } from "../lib/coords";
import { KV, Note, Panel, Term } from "./ui";

const TONE: Record<string, string> = {
  candidate: "bg-orange-500/20 text-orange-200 border-orange-500/50",
  known: "bg-emerald-500/15 text-emerald-200 border-emerald-500/40",
  artifact: "bg-slate-600/30 text-slate-300 border-slate-500/40",
  source: "bg-teal-500/15 text-teal-200 border-teal-500/40",
};

export function toneOf(s: SourceSummary): string {
  if (s.artefact_risk) return "artifact";
  if (s.status === "known") return "known";
  return s.classification !== "stationary" && s.classification !== "insufficient_data" ? "candidate" : "source";
}

function rank(s: SourceSummary): number {
  if (s.is_target) return 0;
  if (s.classification === "insufficient_data") return s.artefact_risk ? 6 : 5;
  const t = toneOf(s);
  return t === "candidate" ? 1 : t === "known" && s.classification !== "stationary" ? 2 : t === "known" ? 3 : t === "source" ? 4 : 5;
}

const fmt = (v: number | null | undefined, nd = 0) => (v == null || !Number.isFinite(v) ? "—" : v.toFixed(nd));
const arcsecYr = (mas?: number | null) => (mas == null ? "—" : `${(mas / 1000).toFixed(2)}″/yr`);

export interface AnalysisPanelProps {
  data: AnalysisResponse | null;
  loading: boolean;
  error: string | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
  productsLoading?: boolean;
}

export function AnalysisPanel({ data, loading, error, selected, onSelect, productsLoading }: AnalysisPanelProps) {
  const rows = useMemo(() => [...(data?.results_summary ?? [])].sort((a, b) => rank(a) - rank(b) || b.confidence - a.confidence), [data]);
  const sel = rows.find((r) => r.source_id === selected) ?? null;
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? rows : rows.filter((r) => !r.artefact_risk).slice(0, 12);

  return (
    <Panel title="Analysis: sources in this field">
      {loading && <p className="text-xs text-slate-400" role="status">Detecting and tracking sources across all visits… (first run on a field takes 10–60 s)</p>}
      {error && <Note tone="error">Analysis unavailable: {error}</Note>}
      {data && !loading && (
        <>
          {data.summary && (
            <p className="text-xs text-slate-300">
              {data.summary.n_sources} sources · {data.summary.n_visits} visits · {data.summary.n_observations} frames
            </p>
          )}
          {data.target && (
            <p className="text-[11px] text-slate-400">
              {data.target.source_id
                ? <>Target found as <button type="button" className="font-mono text-amber-300 hover:underline" onClick={() => onSelect(data.target!.source_id!)}>{data.target.source_id}</button>,
                  {" "}{fmt(data.target.median_offset_from_prediction_arcsec, 2)}″ (median) from its catalogue-predicted position.</>
                : data.target.note ?? "Target not identified in this field."}
            </p>
          )}
          {!rows.length && <p className="text-xs text-slate-500">No sources above 5σ in this field.</p>}
          <ul className="space-y-1" aria-label="Detected sources">
            {shown.map((s) => {
              const t = toneOf(s);
              const m = s.angular_motion;
              const active = s.source_id === selected;
              return (
                <li key={s.source_id}>
                  <button type="button" onClick={() => onSelect(active ? null : s.source_id)} aria-pressed={active}
                    className={`w-full rounded border px-2 py-1 text-left text-xs ${TONE[t]} ${active ? "ring-1 ring-amber-400" : "hover:brightness-125"}`}>
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-semibold">{s.source_id}{s.is_target ? " ★" : ""}</span>
                      <span className="min-w-0 flex-1 truncate">{s.label}</span>
                      <span className="font-mono text-[10px]" title="Confidence: how strongly the criteria are met (not a probability of being new)">{s.confidence.toFixed(2)}</span>
                    </div>
                    <div className="mt-0.5 flex gap-2 text-[10px] text-slate-400">
                      <span>{s.status === "known" ? `known · ${s.known_catalogue_match?.catalog ?? ""}` : t === "artifact" ? "probable artefact" : "no catalogue match"}</span>
                      {m?.pm_total_mas_yr != null && ((m.motion_sigma ?? 0) >= 3
                        ? <span>µ {arcsecYr(m.pm_total_mas_yr)} ({fmt(m.motion_sigma, 1)}σ)</span>
                        : <span>no significant motion</span>)}
                      {s.saturated && <span className="text-amber-300">saturated</span>}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
          {rows.length > shown.length && (
            <button type="button" className="text-[11px] text-sky-300 hover:underline" onClick={() => setShowAll(true)}>
              Show all {rows.length} (incl. {rows.filter((r) => r.artefact_risk).length} probable artefacts)
            </button>
          )}
          {sel && <SourceDetail s={sel} productsLoading={productsLoading} />}
          {!!data.warnings?.length && (
            <details className="text-[11px] text-amber-200/90">
              <summary className="cursor-pointer">Field warnings ({data.warnings.length})</summary>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">{data.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </details>
          )}
          {data.provenance && (
            <p className="text-[10px] text-slate-500">
              Releases {data.provenance.releases?.join(" + ") ?? "?"} · λ from {data.provenance.wavelength_source?.join(", ") ?? "?"} · analysis v{data.analysis_version}
              {data.registration_calibration ? ` · alignment error ${data.registration_calibration.sigma_arcsec}″ (${data.registration_calibration.method})` : ""}
            </p>
          )}
          <p className="text-[10px] leading-snug text-slate-500">{data.disclaimer ?? "Classes are preliminary and criteria-based; a candidate is not a discovery."}</p>
        </>
      )}
    </Panel>
  );
}

function SourceDetail({ s, productsLoading }: { s: SourceSummary; productsLoading?: boolean }) {
  const m = s.angular_motion;
  const km = s.known_catalogue_match;
  const v = s.variability_indicator;
  const [copied, setCopied] = useState(false);
  const report = () => {
    const lines = [
      `cosmIO source report (preliminary; not a confirmed discovery)`,
      `View: ${window.location.href}`,
      `Source ${s.source_id}: ${s.label} (${s.classification}, confidence ${s.confidence.toFixed(2)}, status ${s.status})`,
      `Position (ref MJD ${fmt(s.ref_mjd, 2)}): RA ${s.ra.toFixed(6)} Dec ${s.dec.toFixed(6)} deg`,
      m?.pm_total_mas_yr != null ? `Proper motion: muRA* ${fmt(m.pm_ra_cosdec_mas_yr)} +/- ${fmt(m.pm_ra_cosdec_err_mas_yr)}, muDec ${fmt(m.pm_dec_mas_yr)} +/- ${fmt(m.pm_dec_err_mas_yr)} mas/yr (${fmt(m.motion_sigma, 1)} sigma, ${fmt(m.baseline_yr, 2)} yr baseline)` : "Proper motion: not measured",
      km ? `Catalogue match: ${km.catalog} ${km.id} at ${fmt(km.separation_arcsec, 2)}"` : "Catalogue match: none in the catalogues checked",
      s.note ?? "",
    ];
    navigator.clipboard?.writeText(lines.filter(Boolean).join("\n")).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }).catch(() => undefined);
  };
  return (
    <div className="space-y-2 rounded-lg border border-white/10 bg-slate-950/60 p-2">
      <p className="text-xs font-semibold text-slate-200">{s.source_id} · {s.label}{s.subtype ? ` · ${s.subtype.split(" ")[0].replace(/_/g, " ")}` : ""}</p>
      <dl className="space-y-0.5">
        <KV k="Position" v={`${formatRaHms(s.ra)} ${formatDecDms(s.dec)}`} />
        <KV k="Seen in" v={`${s.n_visits ?? "?"} visits · ${s.n_frame_detections ?? "?"} frames`} />
        <KV k={<Term k="confidence">Confidence</Term>} v={s.confidence.toFixed(2)} />
        {m && m.pm_total_mas_yr != null && <>
          <KV k={<Term k="proper_motion">Proper motion</Term>} v={(m.motion_sigma ?? 0) >= 3
            ? `${arcsecYr(m.pm_total_mas_yr)} ± ${arcsecYr(m.pm_err_mas_yr)}`
            : `not significant (< ${arcsecYr(3 * (m.pm_err_mas_yr ?? 0))} at 3σ)`} />
          <KV k="µα*, µδ (mas/yr)" v={`${fmt(m.pm_ra_cosdec_mas_yr)} ± ${fmt(m.pm_ra_cosdec_err_mas_yr)}, ${fmt(m.pm_dec_mas_yr)} ± ${fmt(m.pm_dec_err_mas_yr)}`} />
          <KV k={<Term k="sigma">Motion significance</Term>} v={`${fmt(m.motion_sigma, 1)}σ · ${m.model ?? ""}`} />
          <KV k="Baseline · moved" v={`${fmt(m.baseline_yr, 2)} yr · ${fmt(m.displacement_arcsec, 1)}″`} />
          {m.linear_chi2_red != null && <KV k="Fit χ²ᵣ" v={fmt(m.linear_chi2_red, 2)} />}
          {m.parallax_removed_mas != null && <KV k="Parallax removed" v={`${fmt(m.parallax_removed_mas, 0)} mas`} />}
        </>}
        {km ? <>
          <KV k={<Term k="known">Catalogue match</Term>} v={`${km.catalog}: ${km.id}`} />
          <KV k="Separation" v={`${fmt(km.separation_arcsec, 2)}″${km.pm_difference_sigma != null ? ` · Δµ ${fmt(km.pm_difference_sigma, 1)}σ` : ""}`} />
        </> : <KV k="Catalogue match" v="none found" />}
        {v && (v.sigma != null || v.reason) && <KV k="Variability" v={v.reliable === false ? `not testable${v.reason ? ": " + v.reason : ""}` : `${fmt(v.sigma, 1)}σ · ${fmt(v.amplitude_mag, 2)} mag`} />}
      </dl>
      {s.saturated && <Note tone="warn"><Term k="saturated">Saturated</Term>: position from the core's centroid; no brightness, light curve or spectrum.</Note>}
      {s.artefact_risk && <Note tone="warn">Probably an <Term k="artefact">artefact</Term> of a nearby bright star.</Note>}
      {!!s.criteria.length && (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-slate-400">Why this class? ({s.criteria.filter((c) => c.passed).length}/{s.criteria.length} criteria met)</summary>
          <table className="mt-1 w-full text-[10px]">
            <thead><tr className="text-slate-500"><th className="text-left">test</th><th className="text-right">value</th><th className="text-right">needs</th><th /></tr></thead>
            <tbody>
              {s.criteria.map((c) => (
                <tr key={c.name} className={c.passed ? "text-slate-300" : "text-slate-500"}>
                  <td>{c.name.replace(/_/g, " ")}{c.required ? "" : " (opt.)"}</td>
                  <td className="text-right font-mono">{c.value == null ? "—" : Math.abs(c.value) >= 100 ? c.value.toFixed(0) : c.value.toPrecision(3)}</td>
                  <td className="text-right font-mono">{c.threshold}{c.unit ? ` ${c.unit}` : ""}</td>
                  <td className="pl-1">{c.passed ? "✓" : "✗"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {s.note && <p className="text-[11px] leading-snug text-slate-400">{s.note}</p>}
      <div className="flex items-center gap-3 text-[11px]">
        <button type="button" onClick={report} className="rounded-lg bg-white/[0.06] px-2 py-1 text-slate-200 hover:bg-white/10">{copied ? "Copied" : "Copy report"}</button>
        <span className="text-slate-500">{productsLoading ? "Loading light curve and spectrum…" : s.has_light_curve ? "Light curve & spectrum shown below." : "No photometry for this source."}</span>
      </div>
    </div>
  );
}
