/** Investigations: the open list, and one investigation with its evidence form and QA result. */
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { measureSpectrum, type SpherexSpectrum } from "../../lib/galaxyData";
import { CATEGORY_HINT, CATEGORY_LABEL, fmtPts, gfetch, post, statusLabel, statusTone, useGame, type Category, type Investigation, type Obs, type SubmitResult } from "../../lib/game";
import { Card, Empty, Kind, NeedSignIn, PageTitle } from "./ScienceLayout";

const CLASSES: [string, string][] = [
  ["ob", "O/B star"], ["a", "A star"], ["f", "F star"], ["g", "G star"], ["k", "K star"], ["m", "M dwarf"], ["bd", "Brown dwarf"],
  ["wd", "White dwarf"], ["giant", "Giant / subgiant"], ["yso", "Young stellar object"], ["galaxy", "Galaxy"], ["artefact", "Artefact, not a source"],
  ["not_source", "Nothing there"], ["unsure", "Unsure"],
];
const FEATURES: [string, string][] = [
  ["h2o_1_4", "H₂O 1.4 µm"], ["h2o_1_9", "H₂O 1.9 µm"], ["ch4_2_2", "CH₄ 2.2 µm"], ["ch4_3_3", "CH₄ 3.3 µm"], ["co_2_3", "CO 2.3 µm"],
  ["co_4_6", "CO 4.6 µm"], ["co2_4_3", "CO₂ 4.3 µm"], ["h2o_ice_3_0", "H₂O ice 3.0 µm"], ["pah_3_3", "PAH 3.3 µm"], ["none", "No features"],
];
const ARTEFACTS: [string, string][] = [["ghost", "Ghost"], ["saturation", "Saturation"], ["bleed", "Bleed trail"], ["cosmic_ray", "Cosmic ray"], ["satellite", "Satellite trail"], ["edge", "Detector edge"], ["other", "Other"]];

export function InvestigateList() {
  const [qs, setQs] = useSearchParams();
  const cat = (qs.get("category") as Category | null) ?? null;
  const [list, setList] = useState<Investigation[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { me } = useGame();
  useEffect(() => {
    setList(null);
    gfetch<{ investigations: Investigation[] }>(`/investigations${cat ? `?category=${cat}` : ""}`).then((r) => setList(r.investigations)).catch((e) => setErr(e.message));
  }, [cat, me?.id]);
  const cats = (Object.keys(CATEGORY_LABEL) as Category[]).filter((c) => c !== "verification");
  return (
    <>
      <PageTitle title="Investigate" sub={cat ? CATEGORY_HINT[cat] : "Open investigations, most useful first: sources no catalogue can classify, then the least investigated."} />
      <div className="seg mb-5 max-w-2xl">
        <button aria-pressed={!cat} onClick={() => setQs({})}>All</button>
        {cats.map((c) => <button key={c} aria-pressed={cat === c} onClick={() => setQs({ category: c })}>{CATEGORY_LABEL[c]}</button>)}
      </div>
      {err && <Empty>{err}</Empty>}
      {list == null && !err ? <Empty>Loading…</Empty> : list && list.length === 0 ? <Empty>Nothing open here right now. You may have done them all.</Empty> : (
        <div className="grid gap-3 md:grid-cols-2">
          {list?.filter((i) => i.category !== "verification").map((i) => (
            <Link key={i.id} to={`/science/investigate/${i.id}`} className="glass group rounded-2xl p-4 transition hover:border-white/15">
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500">{CATEGORY_LABEL[i.category]}</span>
                <span className="ml-auto flex gap-1">
                  {i.data.class_source === "none" && <span className="chip border-fuchsia-300/30 py-0 text-[10.5px] text-fuchsia-200">Uncharted</span>}
                  <Kind k={i.kind === "training" ? "training" : "real"} />
                </span>
              </div>
              <div className="mt-1 truncate text-[15px] font-semibold text-slate-100">{i.source_name}</div>
              <div className="num mt-1 text-[11px] text-slate-500">RA {i.ra.toFixed(4)}° · Dec {i.dec.toFixed(4)}° {i.data.dist_pc != null && `· ${Number(i.data.dist_pc).toFixed(1)} pc`}</div>
              <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                <span>Difficulty {"●".repeat(i.difficulty)}<span className="text-slate-700">{"●".repeat(3 - Math.min(3, i.difficulty))}</span></span>
                <span>{i.n_submissions} submissions</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

export function InvestigationPage() {
  const { id } = useParams();
  const { me, celebrate, refresh } = useGame();
  const [inv, setInv] = useState<Investigation | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const load = (refreshObs = false) => gfetch<Investigation>(`/investigations/${id}${refreshObs ? "?refresh=true" : ""}`).then(setInv).catch((e) => setErr(e.message));
  useEffect(() => { setResult(null); load(); }, [id, me?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (err) return <Empty>{err}</Empty>;
  if (!inv) return <Empty>Loading the investigation and its SPHEREx observations…</Empty>;
  const exploreUrl = `/?view=explore&ra=${inv.ra.toFixed(5)}&dec=${inv.dec.toFixed(5)}&fov=0.05&det=${inv.category === "spectral" ? 6 : 3}`;
  const atlasUrl = inv.data.atlas_index != null ? `/?o=${inv.data.atlas_index}` : "/";

  const submit = async (payload: Record<string, unknown>) => {
    const r = await post<SubmitResult>(`/investigations/${inv.id}/submit`, payload);
    setResult(r);
    celebrate(r.rewards, r.achievements);
    refresh();
    load();
  };

  return (
    <>
      <PageTitle title={inv.source_name}
        sub={<>{CATEGORY_LABEL[inv.category]} investigation · <span className="num">RA {inv.ra.toFixed(5)}°, Dec {inv.dec.toFixed(5)}°</span></>}
        right={<div className="flex gap-2"><a href={atlasUrl} className="btn">Show in 3D Atlas</a><a href={exploreUrl} target="_blank" rel="noreferrer" className="btn btn-primary">Open SPHEREx images ↗</a></div>} />
      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <div className="space-y-6">
          {inv.kind === "training" && <div className="rounded-xl border border-sky-300/25 bg-sky-400/[0.06] px-4 py-3 text-sm text-sky-100">Training task: this source's class is already catalogued. Your answer is checked against it and earns XP for your level, not contribution points.</div>}
          <Card title="What to do"><p className="text-sm leading-relaxed text-slate-300">{inv.instructions}</p></Card>
          {result ? <QcResult r={result} onAgain={() => setResult(null)} /> : !me ? <NeedSignIn what="submit your findings" /> :
            inv.observations == null ? <Empty>The list of SPHEREx images for this field has not been fetched from IRSA yet (the archive did not respond). Submissions open once it is available; use “Try again” beside.</Empty> :
            inv.status !== "open" ? <Empty>This investigation is {statusLabel(inv.status)}{inv.status === "data_unavailable" && ": SPHEREx has not observed this position, so there is nothing to examine."}</Empty> :
            inv.category === "classification" ? <ClassifyForm inv={inv} onSubmit={submit} /> :
            inv.category === "moving" ? <MovingForm inv={inv} onSubmit={submit} /> :
            inv.category === "spectral" ? <SpectralForm inv={inv} onSubmit={submit} /> :
            <ArtefactForm inv={inv} onSubmit={submit} />}
        </div>
        <div className="space-y-6">
          <SourceCard inv={inv} />
          <ObsCard obs={inv.observations} onRetry={() => load(true)} />
          {inv.my_submissions && inv.my_submissions.length > 0 && (
            <Card title="Your submissions">
              <ul className="space-y-1.5 text-xs">{inv.my_submissions.map((s) => (
                <li key={s.id} className="flex justify-between"><span className="num text-slate-400">{s.submitted_at.slice(0, 16).replace("T", " ")}</span><span className={`chip py-0 ${statusTone[s.qc_status]}`}>{statusLabel(s.qc_status)}</span></li>
              ))}</ul>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function SourceCard({ inv }: { inv: Investigation }) {
  const d = inv.data as Record<string, string | number | null>;
  const rows: [string, string | null][] = [
    ["Gaia DR3", d.gaia_dr3_id ? String(d.gaia_dr3_id) : "not in Gaia"], ["Position epoch", d.position_epoch as string],
    ["Distance", d.dist_pc != null ? `${Number(d.dist_pc).toFixed(2)} pc` : null], ["G mag", d.gmag != null ? Number(d.gmag).toFixed(2) : null],
    ["Proper motion", d.pmra_mas_yr != null ? `${Math.hypot(Number(d.pmra_mas_yr), Number(d.pmdec_mas_yr)).toFixed(0)} mas/yr` : null],
    ["μα*, μδ", d.pmra_mas_yr != null ? `${Number(d.pmra_mas_yr).toFixed(1)}, ${Number(d.pmdec_mas_yr).toFixed(1)} mas/yr` : null],
    ["Catalogue class", inv.kind === "training" ? "hidden (training)" : d.class_source === "none" ? "none in any catalogue" : (d.sptype as string) || (d.class_key as string) || null],
  ];
  return (
    <Card title={<span className="flex items-center gap-2">Source <Kind k="real" /></span>}>
      <dl className="space-y-1.5 text-xs">{rows.filter((r) => r[1] != null).map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3"><dt className="text-slate-500">{k}</dt><dd className="num truncate text-right text-slate-200">{v}</dd></div>
      ))}</dl>
    </Card>
  );
}

function ObsCard({ obs, onRetry }: { obs: Obs[] | null; onRetry: () => void }) {
  return (
    <Card title={<span className="flex items-center gap-2">SPHEREx observations <Kind k="real" /></span>}>
      {obs == null ? <div className="space-y-2 text-xs text-slate-400"><p>The SPHEREx archive (IRSA) could not be reached. Nothing has been assumed.</p><button className="btn" onClick={onRetry}>Try again</button></div> :
        obs.length === 0 ? <p className="text-xs text-slate-400">No SPHEREx images of this position yet.</p> : (
        <ul className="max-h-72 space-y-1 overflow-y-auto pr-1 text-xs">
          {obs.map((o) => (
            <li key={o.id} className="flex items-center gap-2 rounded-lg px-2 py-1 hover:bg-white/[0.04]" title={o.id}>
              <span className="num w-[86px] shrink-0 text-slate-400">{o.time?.slice(0, 10) ?? "?"}</span>
              <span className="num shrink-0 text-slate-500">D{o.detector ?? "?"}</span>
              <span className="num min-w-0 flex-1 truncate text-slate-300">{o.id}</span>
              {o.quality_flags.length > 0 && <span className="text-amber-300" title={o.quality_flags.join(", ")}>⚑</span>}
            </li>
          ))}
        </ul>
      )}
      {obs && obs.length > 0 && <p className="mt-2 text-[11px] text-slate-500">{obs.length} observations · IDs as listed by NASA/IPAC IRSA</p>}
    </Card>
  );
}

function ObsPicker({ obs, value, onChange }: { obs: Obs[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs text-slate-400"><span>Observations you examined</span>
        <button type="button" className="text-sky-200 hover:text-white" onClick={() => onChange(value.length === obs.length ? [] : obs.map((o) => o.id))}>{value.length === obs.length ? "None" : "All"}</button></div>
      <div className="max-h-40 space-y-0.5 overflow-y-auto rounded-xl border border-white/[0.06] p-1.5">
        {obs.map((o) => (
          <label key={o.id} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1 text-xs hover:bg-white/[0.04]">
            <input type="checkbox" checked={value.includes(o.id)} onChange={(e) => onChange(e.target.checked ? [...value, o.id] : value.filter((x) => x !== o.id))} />
            <span className="num text-slate-400">{o.time?.slice(0, 10)}</span><span className="num text-slate-500">D{o.detector}</span><span className="num truncate text-slate-300">{o.id}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

function FormShell({ children, onSubmit, ready, note }: { children: React.ReactNode; onSubmit: () => Promise<void>; ready: boolean; note?: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Card title="Your findings">
      <form className="space-y-4" onSubmit={async (e) => { e.preventDefault(); setBusy(true); setErr(null); try { await onSubmit(); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); } }}>
        {children}
        {err && <p className="rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">{err}</p>}
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] text-slate-500">{note ?? "The server checks your submission automatically and decides any points. Nothing is scored in the browser."}</p>
          <button className="btn btn-primary shrink-0 px-5" disabled={busy || !ready}>{busy ? "Checking…" : "Submit"}</button>
        </div>
      </form>
    </Card>
  );
}

const Notes = ({ v, set }: { v: string; set: (s: string) => void }) => (
  <label className="block text-xs text-slate-400">Notes <span className="text-slate-600">(what you saw, how you handled flagged frames)</span>
    <textarea className="field mt-1 min-h-[72px]" value={v} onChange={(e) => set(e.target.value)} maxLength={2000} />
  </label>
);

function ClassifyForm({ inv, onSubmit }: { inv: Investigation; onSubmit: (p: Record<string, unknown>) => Promise<void> }) {
  const [label, setLabel] = useState<string>("");
  const [conf, setConf] = useState(0.6);
  const [obs, setObs] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  return (
    <FormShell ready={!!label} onSubmit={() => onSubmit({ label, confidence: conf, observations: obs, notes })}>
      <div>
        <div className="mb-1.5 text-xs text-slate-400">Class</div>
        <div className="flex flex-wrap gap-1.5">{CLASSES.map(([k, l]) => <button type="button" key={k} onClick={() => setLabel(k)} className={`chip ${label === k ? "chip-on" : "hover:text-slate-200"}`}>{l}</button>)}</div>
      </div>
      <label className="block text-xs text-slate-400">Confidence <span className="num text-slate-200">{Math.round(conf * 100)}%</span>
        <input type="range" min={0} max={1} step={0.05} value={conf} onChange={(e) => setConf(Number(e.target.value))} className="mt-1 w-full" /></label>
      {inv.observations && inv.observations.length > 0 && <ObsPicker obs={inv.observations} value={obs} onChange={setObs} />}
      <Notes v={notes} set={setNotes} />
    </FormShell>
  );
}

interface M { obs_id: string; ra: string; dec: string; unc_arcsec: string }
function MovingForm({ inv, onSubmit }: { inv: Investigation; onSubmit: (p: Record<string, unknown>) => Promise<void> }) {
  const obs = inv.observations ?? [];
  const blank = (k: number): M => ({ obs_id: obs[k === 0 ? 0 : obs.length - 1]?.id ?? "", ra: "", dec: "", unc_arcsec: "2" });
  const [ms, setMs] = useState<M[]>([blank(0), blank(1)]);
  const [moved, setMoved] = useState<boolean | null>(null);
  const [notes, setNotes] = useState("");
  const set = (k: number, f: keyof M, v: string) => setMs((x) => x.map((m, j) => (j === k ? { ...m, [f]: v } : m)));
  const ready = ms.length >= 2 && ms.every((m) => m.obs_id && m.ra && m.dec);
  return (
    <FormShell ready={ready} onSubmit={() => onSubmit({ measurements: ms.map((m) => ({ obs_id: m.obs_id, ra: Number(m.ra), dec: Number(m.dec), unc_arcsec: Number(m.unc_arcsec) })), moved, notes, observations: ms.map((m) => m.obs_id) })}
      note="Measure positions in the image viewer (Analysis panel). Your measured motion is compared with the Gaia proper motion of this star.">
      <div className="space-y-2">
        <div className="grid grid-cols-[1fr_110px_110px_80px_24px] gap-2 text-[11px] text-slate-500"><span>Observation</span><span>RA (deg)</span><span>Dec (deg)</span><span>± (″)</span><span /></div>
        {ms.map((m, k) => (
          <div key={k} className="grid grid-cols-[1fr_110px_110px_80px_24px] gap-2">
            <select className="field py-1.5 text-xs" value={m.obs_id} onChange={(e) => set(k, "obs_id", e.target.value)}>
              {obs.map((o) => <option key={o.id} value={o.id}>{o.time?.slice(0, 10)} · D{o.detector} · {o.id.slice(-18)}</option>)}
            </select>
            <input className="field num py-1.5 text-xs" inputMode="decimal" placeholder={inv.ra.toFixed(5)} value={m.ra} onChange={(e) => set(k, "ra", e.target.value)} />
            <input className="field num py-1.5 text-xs" inputMode="decimal" placeholder={inv.dec.toFixed(5)} value={m.dec} onChange={(e) => set(k, "dec", e.target.value)} />
            <input className="field num py-1.5 text-xs" inputMode="decimal" value={m.unc_arcsec} onChange={(e) => set(k, "unc_arcsec", e.target.value)} />
            <button type="button" className="text-slate-500 hover:text-white disabled:opacity-30" disabled={ms.length <= 2} onClick={() => setMs((x) => x.filter((_, j) => j !== k))} aria-label="Remove">✕</button>
          </div>
        ))}
        <button type="button" className="btn text-xs" onClick={() => setMs((x) => [...x, blank(1)])}>+ Add epoch</button>
      </div>
      <div className="text-xs text-slate-400">Did it move between the dates?
        <div className="seg mt-1 max-w-xs"><button type="button" aria-pressed={moved === true} onClick={() => setMoved(true)}>Moved</button><button type="button" aria-pressed={moved === false} onClick={() => setMoved(false)}>Did not move</button></div>
      </div>
      <Notes v={notes} set={setNotes} />
    </FormShell>
  );
}

function SpectralForm({ inv, onSubmit }: { inv: Investigation; onSubmit: (p: Record<string, unknown>) => Promise<void> }) {
  const [spx, setSpx] = useState<SpherexSpectrum | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [feats, setFeats] = useState<string[]>([]);
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState("");
  const idx = inv.data.atlas_index as number | undefined;
  const measure = async () => { if (idx == null) return; setBusy(true); setErr(null); try { setSpx(await measureSpectrum(idx)); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };
  return (
    <FormShell ready={!!spx && feats.length > 0} onSubmit={() => onSubmit({ spectrum_measured: !!spx, features: feats, label: label || undefined, notes, observations: [] })}>
      <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-xs">
        {spx ? <SpectrumMini s={spx} /> : (
          <div className="flex items-center justify-between gap-3"><span className="text-slate-400">Measure this source's 0.75–5 µm spectrum from every SPHEREx image of it (1–3 minutes the first time).</span>
            <button type="button" className="btn shrink-0" disabled={busy || idx == null} onClick={measure}>{busy ? "Measuring…" : "Measure spectrum"}</button></div>
        )}
        {err && <p className="mt-2 text-red-300">{err}</p>}
      </div>
      <div><div className="mb-1.5 text-xs text-slate-400">Absorption features you see</div>
        <div className="flex flex-wrap gap-1.5">{FEATURES.map(([k, l]) => <button type="button" key={k} onClick={() => setFeats((f) => (f.includes(k) ? f.filter((x) => x !== k) : [...f, k]))} className={`chip ${feats.includes(k) ? "chip-on" : "hover:text-slate-200"}`}>{l}</button>)}</div></div>
      <div><div className="mb-1.5 text-xs text-slate-400">The spectrum suggests <span className="text-slate-600">(optional)</span></div>
        <div className="flex flex-wrap gap-1.5">{CLASSES.slice(0, 11).map(([k, l]) => <button type="button" key={k} onClick={() => setLabel(label === k ? "" : k)} className={`chip ${label === k ? "chip-on" : "hover:text-slate-200"}`}>{l}</button>)}</div></div>
      <Notes v={notes} set={setNotes} />
    </FormShell>
  );
}

function SpectrumMini({ s }: { s: SpherexSpectrum }) {
  const pts = useMemo(() => s.spectrum.filter((p) => p.f != null && Number.isFinite(p.f)), [s]);
  if (s.status !== "ok" || !pts.length) return <p className="text-slate-400">No usable spectrum ({s.status.replace("_", " ")}, {s.n_frames} frames). Describe what you can and choose "No features" if appropriate.</p>;
  const W = 520, H = 150, l = (w: number) => 30 + ((Math.log(w) - Math.log(0.74)) / (Math.log(5.05) - Math.log(0.74))) * (W - 40);
  const fmax = Math.max(...pts.map((p) => p.f)) * 1.1, fmin = Math.min(0, ...pts.map((p) => p.f));
  const y = (f: number) => 8 + (1 - (f - fmin) / (fmax - fmin)) * (H - 30);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {[[1.33, 1.45, "H₂O"], [1.8, 2.0, "H₂O"], [2.15, 2.4, "CH₄/CO"], [3.2, 3.4, "CH₄"], [4.2, 4.4, "CO₂"], [4.5, 4.8, "CO"]].map(([a, b, t], k) => (
        <g key={k}><rect x={l(a as number)} y={8} width={l(b as number) - l(a as number)} height={H - 30} fill="#8fd8f8" opacity={0.06} /><text x={(l(a as number) + l(b as number)) / 2} y={18} fontSize={8} fill="#8fd8f8" textAnchor="middle">{t}</text></g>
      ))}
      {[0.8, 1, 1.5, 2, 3, 4, 5].map((w) => <text key={w} x={l(w)} y={H - 6} fontSize={8} fill="#6c748a" textAnchor="middle">{w}</text>)}
      {pts.map((p, k) => <g key={k}>{p.e != null && <line x1={l(p.w)} x2={l(p.w)} y1={y(p.f - p.e)} y2={y(p.f + p.e)} stroke="#8fd8f8" opacity={0.35} />}<circle cx={l(p.w)} cy={y(p.f)} r={1.8} fill="#e6e9f2" /></g>)}
      <text x={W - 6} y={H - 6} fontSize={8} fill="#6c748a" textAnchor="end">µm</text>
    </svg>
  );
}

interface F { obs_id: string; artefact: string; note: string }
function ArtefactForm({ inv, onSubmit }: { inv: Investigation; onSubmit: (p: Record<string, unknown>) => Promise<void> }) {
  const obs = inv.observations ?? [];
  const [clean, setClean] = useState(false);
  const [fs, setFs] = useState<F[]>([]);
  const [seen, setSeen] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  return (
    <FormShell ready={clean || (fs.length > 0 && fs.every((f) => f.obs_id && f.artefact))}
      onSubmit={() => onSubmit({ clean, findings: clean ? [] : fs, observations: seen, notes })}>
      <div className="seg max-w-sm"><button type="button" aria-pressed={!clean} onClick={() => setClean(false)}>I found artefacts</button><button type="button" aria-pressed={clean} onClick={() => { setClean(true); setFs([]); }}>Frames are clean</button></div>
      {!clean && (
        <div className="space-y-2">
          {fs.map((f, k) => (
            <div key={k} className="grid grid-cols-[1fr_150px_1fr_24px] gap-2">
              <select className="field py-1.5 text-xs" value={f.obs_id} onChange={(e) => setFs((x) => x.map((y, j) => (j === k ? { ...y, obs_id: e.target.value } : y)))}>
                <option value="">Observation…</option>{obs.map((o) => <option key={o.id} value={o.id}>{o.time?.slice(0, 10)} · D{o.detector} · {o.id.slice(-18)}</option>)}
              </select>
              <select className="field py-1.5 text-xs" value={f.artefact} onChange={(e) => setFs((x) => x.map((y, j) => (j === k ? { ...y, artefact: e.target.value } : y)))}>
                <option value="">Type…</option>{ARTEFACTS.map(([a, l]) => <option key={a} value={a}>{l}</option>)}
              </select>
              <input className="field py-1.5 text-xs" placeholder="Where in the frame" value={f.note} onChange={(e) => setFs((x) => x.map((y, j) => (j === k ? { ...y, note: e.target.value } : y)))} />
              <button type="button" className="text-slate-500 hover:text-white" onClick={() => setFs((x) => x.filter((_, j) => j !== k))} aria-label="Remove">✕</button>
            </div>
          ))}
          <button type="button" className="btn text-xs" onClick={() => setFs((x) => [...x, { obs_id: "", artefact: "", note: "" }])}>+ Add artefact</button>
        </div>
      )}
      {obs.length > 0 && <ObsPicker obs={obs} value={seen} onChange={setSeen} />}
      <Notes v={notes} set={setNotes} />
    </FormShell>
  );
}

export function QcResult({ r, onAgain }: { r: SubmitResult; onAgain?: () => void }) {
  const q = r.qc;
  const delta = r.totals.contribution_xp - r.totals_before.contribution_xp;
  const rb = r.rank_before.all, ra = r.rank.all;
  return (
    <Card title={<span className="flex items-center gap-2">Automated quality check <span className={`chip py-0 ${statusTone[q.status]}`}>{statusLabel(q.status)}</span></span>}
      actions={onAgain && <button className="btn text-xs" onClick={onAgain}>Back</button>}>
      <div className="grid gap-4 sm:grid-cols-3">
        <div><div className="text-[11px] text-slate-500">Contribution points</div><div className="num text-xl text-white">{delta > 0 ? `+${fmtPts(delta)}` : "0"}</div></div>
        <div><div className="text-[11px] text-slate-500">Evidence score</div><div className="num text-xl text-white">{Math.round(q.evidence_score * 100)}%{q.high_quality && <span className="ml-1 text-xs text-emerald-300">high quality</span>}</div></div>
        <div><div className="text-[11px] text-slate-500">Global rank</div><div className="num text-xl text-white">{ra ? `#${ra}` : "–"}{rb && ra && rb > ra && <span className="ml-1 text-xs text-emerald-300">↑{rb - ra}</span>}{!rb && ra && <span className="ml-1 text-xs text-emerald-300">new</span>}</div></div>
      </div>
      {r.training && q.training_correct != null && <p className={`mt-4 text-sm ${q.training_correct ? "text-emerald-200" : "text-amber-200"}`}>{q.training_correct ? "Correct: matches the catalogued class." : "Not the catalogued class. Compare its colours with a few known stars and try another training task."}</p>}
      {q.significant_motion != null && <p className="mt-4 text-sm text-slate-300">Measured displacement <span className="num">{String(q.displacement_arcsec)}″</span> ({String(q.displacement_sigma)}σ){q.predicted_displacement_arcsec != null && <>; Gaia predicts <span className="num">{String(q.predicted_displacement_arcsec)}″</span> over <span className="num">{String(q.baseline_years)}</span> yr</>}.</p>}
      <ul className="mt-4 space-y-1 text-xs">
        {q.checks.map((c, k) => (
          <li key={k} className="flex gap-2">
            <span className={c.result === "pass" ? "text-emerald-300" : c.result === "skipped" ? "text-slate-500" : c.severity === "warning" ? "text-amber-300" : "text-red-300"}>{c.result === "pass" ? "✓" : c.result === "skipped" ? "–" : "!"}</span>
            <span className="text-slate-400"><span className="text-slate-300">{c.check.replace(/_/g, " ")}</span>: {c.detail}</span>
          </li>
        ))}
      </ul>
      <p className="mt-4 border-t border-white/[0.06] pt-3 text-[11px] text-slate-500">{q.disclaimer} {q.status.startsWith("passed") && !r.training && "Next: other investigators review it blind."}</p>
    </Card>
  );
}
