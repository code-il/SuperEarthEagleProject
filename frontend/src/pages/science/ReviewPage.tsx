/** Blind community review: assess first, see the original answer only afterwards. */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CATEGORY_LABEL, gfetch, post, useGame, type Investigation, type Obs, type Reward, type Achievement } from "../../lib/game";
import { Card, Empty, Kind, NeedSignIn, PageTitle } from "./ScienceLayout";

interface Blind {
  candidate_id: string; category: string; ra: number; dec: number; source_name: string; observations: Obs[] | null;
  catalogue: Record<string, string | number | null>; evidence: Record<string, unknown>[]; min_seconds: number; labels: string[];
}
interface Outcome {
  agrees: boolean; your_label: string; revealed: { consensus_label: string | null; labels: Record<string, number> | null };
  agreement: { reviews: number; agree: number; disagree: number; agreement: number | null; agreement_lower_95: number | null; note: string };
  rewards: Reward[]; achievements: Achievement[];
}
const REASONS: [string, string][] = [
  ["evidence_clear", "The evidence clearly supports my answer"], ["consistent_motion", "Motion is consistent across epochs"], ["spectral_match", "Spectral features match"],
  ["artefact_visible", "The artefact is visible"], ["insufficient_evidence", "Not enough evidence"], ["wrong_class", "A different class fits better"],
  ["artefact_not_source", "It is an artefact, not a source"], ["no_motion", "No significant motion"], ["motion_inconsistent", "Motion is inconsistent"],
  ["poor_data_quality", "Data quality too poor"], ["wrong_observation", "Wrong or irrelevant observations"], ["other", "Other (explain in notes)"],
];

export function ReviewQueue() {
  const { me } = useGame();
  const [tasks, setTasks] = useState<Investigation[] | null>(null);
  useEffect(() => { if (me) gfetch<{ tasks: Investigation[] }>("/review/queue").then((r) => setTasks(r.tasks)).catch(() => setTasks([])); }, [me]);
  return (
    <>
      <PageTitle title="Review" sub="Assess other investigators' candidates independently. You will not see their answer, or who they are, until you have given yours. You cannot review your own work." />
      {!me ? <NeedSignIn what="review candidates" /> : tasks == null ? <Empty>Loading…</Empty> : tasks.length === 0 ? <Empty>No candidates need your review right now.</Empty> : (
        <div className="grid gap-3 md:grid-cols-2">
          {tasks.map((t) => (
            <Link key={t.id} to={`/science/review/${t.candidate_id}`} className="glass rounded-2xl p-4 transition hover:border-white/15">
              <div className="text-xs text-slate-500">{CATEGORY_LABEL[(t.data.candidate_category as keyof typeof CATEGORY_LABEL) ?? "classification"]} candidate</div>
              <div className="mt-1 text-[15px] font-semibold text-slate-100">{t.source_name.replace("Verify ", "")}</div>
              <div className="mt-2 text-[11px] text-slate-500">{t.n_reviews ?? 0} reviews so far</div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

export function ReviewCandidate() {
  const { id } = useParams();
  const { me, celebrate, refresh } = useGame();
  const [v, setV] = useState<Blind | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [t0] = useState(() => Date.now());
  const [now, setNow] = useState(Date.now());
  const [label, setLabel] = useState("");
  const [reason, setReason] = useState("");
  const [quality, setQuality] = useState("adequate");
  const [conf, setConf] = useState(0.6);
  const [notes, setNotes] = useState("");
  const [out, setOut] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (me) gfetch<Blind>(`/review/${id}`).then(setV).catch((e) => setErr(e.message)); }, [id, me]);
  useEffect(() => { const k = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(k); }, []);
  if (!me) return <NeedSignIn what="review candidates" />;
  if (err) return <Empty>{err} <Link className="text-sky-200" to="/science/review">Back to the queue</Link></Empty>;
  if (!v) return <Empty>Loading the evidence…</Empty>;
  const wait = Math.max(0, v.min_seconds - (now - t0) / 1000);

  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await post<Outcome>(`/review/${id}`, { label, reason_code: reason, data_quality: quality, confidence: conf, notes });
      setOut(r); celebrate(r.rewards, r.achievements); refresh();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <>
      <PageTitle title={`Review: ${v.source_name}`} sub={<>Blind review of a {v.category.replace("_", " ")} candidate · <span className="num">RA {v.ra.toFixed(5)}°, Dec {v.dec.toFixed(5)}°</span></>}
<<<<<<< HEAD
        right={<a className="btn btn-primary" target="_blank" rel="noreferrer" href={`/map?view=explore&ra=${v.ra.toFixed(5)}&dec=${v.dec.toFixed(5)}&fov=0.05`}>Open SPHEREx images ↗</a>} />
=======
        right={<a className="btn btn-primary" target="_blank" rel="noreferrer" href={`/?view=explore&ra=${v.ra.toFixed(5)}&dec=${v.dec.toFixed(5)}&fov=0.05`}>Open SPHEREx images ↗</a>} />
>>>>>>> 15015553c6e704fff68626c1145fa538697a9c25
      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <div className="space-y-6">
          <Card title={<span className="flex items-center gap-2">Evidence submitted <Kind k="community" /></span>}>
            <div className="space-y-3">
              {v.evidence.map((e, k) => (
                <div key={k} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-xs">
                  <div className="mb-1 flex justify-between"><span className="font-medium text-slate-200">{String(e.submission)}</span><span className="num text-slate-500">evidence {Math.round(Number(e.evidence_score ?? 0) * 100)}%</span></div>
                  <pre className="num max-h-48 overflow-auto whitespace-pre-wrap text-[11px] text-slate-400">{JSON.stringify(Object.fromEntries(Object.entries(e).filter(([k2]) => !["submission", "evidence_score"].includes(k2))), null, 1)}</pre>
                </div>
              ))}
            </div>
          </Card>
          {out ? (
            <Card title="Your review is recorded">
              <p className={`text-sm ${out.agrees ? "text-emerald-200" : "text-amber-200"}`}>{out.agrees ? "You agreed with the investigators' answer" : "You reached a different answer"}: you said <b>{out.your_label}</b>, the submitted answer was <b>{out.revealed.consensus_label ?? "none"}</b>.</p>
              <dl className="mt-4 grid grid-cols-3 gap-4 text-sm">
                <div><dt className="text-[11px] text-slate-500">Reviews</dt><dd className="num text-white">{out.agreement.reviews}</dd></div>
                <div><dt className="text-[11px] text-slate-500">Agreement</dt><dd className="num text-white">{out.agreement.agreement != null ? `${Math.round(out.agreement.agreement * 100)}%` : "–"}</dd></div>
                <div><dt className="text-[11px] text-slate-500">95% lower bound</dt><dd className="num text-white">{out.agreement.agreement_lower_95 != null ? `${Math.round(out.agreement.agreement_lower_95 * 100)}%` : "–"}</dd></div>
              </dl>
              <p className="mt-3 text-[11px] text-slate-500">{out.agreement.note}</p>
              <Link to="/science/review" className="btn mt-4">Next candidate</Link>
            </Card>
          ) : (
            <Card title="Your independent assessment">
              <div className="space-y-4">
                <div><div className="mb-1.5 text-xs text-slate-400">Your answer</div>
                  <div className="flex flex-wrap gap-1.5">{v.labels.map((l) => <button key={l} onClick={() => setLabel(l)} className={`chip ${label === l ? "chip-on" : "hover:text-slate-200"}`}>{l.replace(/_/g, " ")}</button>)}</div></div>
                <label className="block text-xs text-slate-400">Main reason
                  <select className="field mt-1" value={reason} onChange={(e) => setReason(e.target.value)}><option value="">Choose…</option>{REASONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="text-xs text-slate-400">Data quality<div className="seg mt-1">{["poor", "adequate", "good"].map((q) => <button key={q} aria-pressed={quality === q} onClick={() => setQuality(q)} className="capitalize">{q}</button>)}</div></div>
                  <label className="block text-xs text-slate-400">Confidence <span className="num text-slate-200">{Math.round(conf * 100)}%</span><input type="range" min={0} max={1} step={0.05} value={conf} onChange={(e) => setConf(Number(e.target.value))} className="mt-2 w-full" /></label>
                </div>
                <label className="block text-xs text-slate-400">Notes<textarea className="field mt-1 min-h-[64px]" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} /></label>
                {err && <p className="rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">{err}</p>}
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[11px] text-slate-500">{wait > 0 ? `Take a proper look first: ${Math.ceil(wait)} s` : "Ready when you are."}</span>
                  <button className="btn btn-primary px-5" disabled={!label || !reason || wait > 0 || busy} onClick={submit}>{busy ? "…" : "Submit review"}</button>
                </div>
              </div>
            </Card>
          )}
        </div>
        <div className="space-y-6">
          <Card title={<span className="flex items-center gap-2">Catalogue <Kind k="real" /></span>}>
            <dl className="space-y-1.5 text-xs">{Object.entries(v.catalogue).filter(([, x]) => x != null).map(([k, x]) => (
              <div key={k} className="flex justify-between gap-3"><dt className="text-slate-500">{k.replace(/_/g, " ")}</dt><dd className="num truncate text-slate-200">{typeof x === "number" ? (Number.isInteger(x) ? x : x.toFixed(3)) : x}</dd></div>
            ))}</dl>
          </Card>
          <Card title="SPHEREx observations">
            {!v.observations?.length ? <p className="text-xs text-slate-400">None listed.</p> : (
              <ul className="max-h-64 space-y-1 overflow-y-auto text-xs">{v.observations.map((o) => <li key={o.id} className="flex gap-2"><span className="num text-slate-400">{o.time?.slice(0, 10)}</span><span className="num truncate text-slate-300">{o.id}</span></li>)}</ul>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
