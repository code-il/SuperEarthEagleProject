/** Leaderboards (two, never mixed), global progress and the scientific contributions pipeline. */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CATEGORY_LABEL, fmtPts, gfetch, useGame, type Category } from "../../lib/game";
import { Card, Empty, Kind, PageTitle, Stat } from "./ScienceLayout";

interface Entry { rank: number; user_id: number; name: string; points: number; valid_contributions: number; reviews: number; streak: number; level: number; rank_change: number | null }
interface Board { entries: Entry[]; me: Entry | null; nearby: Entry[] | null; total_ranked: number; updated_at: string; note: string }
interface VEntry { rank: number; user_id: number; name: string; validated_contributions: number; outcomes: string[]; organisations: string[]; last_reviewed: string }
interface VBoard { entries: VEntry[]; note: string; simulated: boolean }

export function LeaderboardPage() {
  const [qs, setQs] = useSearchParams();
  const tab = qs.get("board") === "validated" ? "validated" : "global";
  return (
    <>
      <PageTitle title="Leaderboards" sub="Two separate boards. The global board counts participation that passed automated checks. The validated board counts only work accepted by an external scientific review." />
      <div className="seg mb-5 max-w-md">
        <button aria-pressed={tab === "global"} onClick={() => setQs({})}>Global contribution</button>
        <button aria-pressed={tab === "validated"} onClick={() => setQs({ board: "validated" })}>Scientifically validated</button>
      </div>
      {tab === "global" ? <GlobalBoard /> : <ValidatedBoard />}
    </>
  );
}

function GlobalBoard() {
  const { me } = useGame();
  const [period, setPeriod] = useState<"all" | "week" | "month">("all");
  const [cat, setCat] = useState<Category | "">("");
  const [b, setB] = useState<Board | null>(null);
  useEffect(() => {
    const load = () => gfetch<Board>(`/leaderboard?period=${period}${cat ? `&category=${cat}` : ""}`).then(setB).catch(() => {});
    load();
    const k = setInterval(load, 15000);
    return () => clearInterval(k);
  }, [period, cat, me?.id]);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-3">
        <div className="seg">{(["all", "month", "week"] as const).map((p) => <button key={p} aria-pressed={period === p} onClick={() => setPeriod(p)}>{p === "all" ? "All time" : p === "month" ? "This month" : "This week"}</button>)}</div>
        <select className="field w-auto py-1.5 text-xs" value={cat} onChange={(e) => setCat(e.target.value as Category | "")} aria-label="Category">
          <option value="">All categories</option>{(Object.keys(CATEGORY_LABEL) as Category[]).map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
        </select>
      </div>
      {b?.me && b.nearby && (
        <Card title="Around you">
          <Table rows={b.nearby} me={me?.id} />
        </Card>
      )}
      <Card title="Top investigators" actions={<span className="text-[11px] text-slate-500">{b ? `${b.total_ranked} ranked · live` : ""}</span>}>
        {!b ? <Empty>Loading…</Empty> : b.entries.length === 0 ? <Empty>No one has scored in this period yet. Be the first.</Empty> : <Table rows={b.entries} me={me?.id} />}
        {b && <p className="mt-4 text-[11px] text-slate-500">{b.note} Weeks start Monday 00:00 UTC.</p>}
      </Card>
    </div>
  );
}

function Table({ rows, me }: { rows: Entry[]; me?: number }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead><tr className="text-left text-[11px] text-slate-500"><th className="pb-2 pr-3 font-normal">#</th><th className="pb-2 pr-3 font-normal">Investigator</th><th className="pb-2 pr-3 text-right font-normal">Points</th><th className="pb-2 pr-3 text-right font-normal">Valid</th><th className="pb-2 pr-3 text-right font-normal">Reviews</th><th className="pb-2 pr-3 text-right font-normal">Streak</th><th className="pb-2 text-right font-normal">Level</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.user_id} className={`border-t border-white/[0.05] ${r.user_id === me ? "bg-sky-300/[0.06]" : ""}`}>
              <td className="num py-2 pr-3 text-slate-400">{r.rank}{r.rank_change ? <span className={`ml-1 text-[10px] ${r.rank_change > 0 ? "text-emerald-300" : "text-red-300"}`}>{r.rank_change > 0 ? `↑${r.rank_change}` : `↓${-r.rank_change}`}</span> : null}</td>
              <td className="py-2 pr-3"><Link to={`/science/players/${r.user_id}`} className="text-slate-100 hover:text-white">{r.name}</Link>{r.user_id === me && <span className="ml-2 text-[11px] text-sky-200">you</span>}</td>
              <td className="num py-2 pr-3 text-right text-white">{fmtPts(r.points)}</td>
              <td className="num py-2 pr-3 text-right text-slate-300">{r.valid_contributions}</td>
              <td className="num py-2 pr-3 text-right text-slate-300">{r.reviews}</td>
              <td className="num py-2 pr-3 text-right text-slate-300">{r.streak} d</td>
              <td className="num py-2 text-right text-slate-300">{r.level}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ValidatedBoard() {
  const [real, setReal] = useState<VBoard | null>(null);
  const [sim, setSim] = useState<VBoard | null>(null);
  useEffect(() => {
    gfetch<VBoard>("/leaderboard/validated").then(setReal).catch(() => {});
    gfetch<VBoard>("/leaderboard/validated?simulated=true").then(setSim).catch(() => {});
  }, []);
  return (
    <div className="space-y-5">
      <Card title={<span className="flex items-center gap-2">Scientifically validated <Kind k="real" /></span>}>
        {!real ? <Empty>Loading…</Empty> : real.entries.length === 0 ? (
          <Empty>No contributions have been validated by an external scientific review yet. This board stays empty until a review organisation accepts a candidate; community agreement does not count.</Empty>
        ) : <VTable rows={real.entries} />}
        {real && <p className="mt-4 text-[11px] text-slate-500">{real.note}</p>}
      </Card>
      {sim && sim.entries.length > 0 && (
        <Card title={<span className="flex items-center gap-2">Demonstration only <Kind k="simulated" /></span>} className="border-fuchsia-300/20">
          <VTable rows={sim.entries} />
          <p className="mt-4 text-[11px] text-fuchsia-200/80">{sim.note}</p>
        </Card>
      )}
    </div>
  );
}

function VTable({ rows }: { rows: VEntry[] }) {
  return (
    <table className="w-full text-sm">
      <thead><tr className="text-left text-[11px] text-slate-500"><th className="pb-2 font-normal">#</th><th className="pb-2 font-normal">Investigator</th><th className="pb-2 text-right font-normal">Validated</th><th className="pb-2 pl-3 font-normal">Reviewed by</th><th className="pb-2 text-right font-normal">Last</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.user_id} className="border-t border-white/[0.05]">
          <td className="num py-2 text-slate-400">{r.rank}</td><td className="py-2 text-slate-100">{r.name}</td><td className="num py-2 text-right text-white">{r.validated_contributions}</td>
          <td className="py-2 pl-3 text-xs text-slate-400">{r.organisations.join(", ")}</td><td className="num py-2 text-right text-xs text-slate-500">{r.last_reviewed?.slice(0, 10)}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

// ------------------------------------------------------------------------ global progress
interface Prog { totals: Record<string, number | null>; milestones: { metric: string; value: number; reached: number[]; next: number | null; progress: number | null }[]; daily: { day: string; n: number }[] }
const NAMES: Record<string, string> = { investigations_completed: "Investigations completed", independent_reviews: "Independent reviews", uncharted_sources_classified: "Uncharted sources classified" };

export function ProgressPage() {
  const [p, setP] = useState<Prog | null>(null);
  useEffect(() => { gfetch<Prog>("/progress").then(setP).catch(() => {}); }, []);
  if (!p) return <Empty>Loading…</Empty>;
  const t = p.totals;
  const max = Math.max(1, ...p.daily.map((d) => d.n));
  return (
    <>
      <PageTitle title="Global progress" sub="What the whole community has done so far. Counts include only work that passed the automated checks; training tasks are excluded." />
      <div className="glass mb-6 grid grid-cols-2 gap-5 rounded-2xl p-5 md:grid-cols-4">
        <Stat k="Investigators" v={fmtPts(t.investigators)} />
        <Stat k="Investigations" v={fmtPts(t.investigations_completed)} />
        <Stat k="Moving-object measurements" v={fmtPts(t.moving_measurements)} />
        <Stat k="Spectra examined" v={fmtPts(t.spectra_examined)} />
        <Stat k="Artefact checks" v={fmtPts(t.artefact_checks)} />
        <Stat k="Independent reviews" v={fmtPts(t.independent_reviews)} />
        <Stat k="Candidates in review" v={fmtPts(t.candidates_in_review)} />
        <Stat k="Externally validated" v={fmtPts(t.external_validations)} />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Milestones">
          <ul className="space-y-4">{p.milestones.map((m) => (
            <li key={m.metric}>
              <div className="flex justify-between text-sm"><span className="text-slate-200">{NAMES[m.metric] ?? m.metric}</span><span className="num text-slate-400">{m.value.toLocaleString()}{m.next != null && ` / ${m.next.toLocaleString()}`}</span></div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${(m.progress ?? 1) * 100}%` }} /></div>
            </li>
          ))}</ul>
          {t.uncharted_sources_in_atlas != null && <p className="mt-5 text-xs text-slate-400"><span className="num text-slate-200">{fmtPts(t.uncharted_sources_in_atlas)}</span> sources in the Atlas still have no class in any catalogue.</p>}
        </Card>
        <Card title="Investigations per day (last 30 active days)">
          {p.daily.length === 0 ? <Empty>No activity yet.</Empty> : (
            <div className="flex h-40 items-end gap-1" role="img" aria-label="Investigations per day">
              {p.daily.map((d) => <div key={d.day} title={`${d.day}: ${d.n}`} className="flex-1 rounded-t bg-[var(--accent)]/70 hover:bg-[var(--accent)]" style={{ height: `${(d.n / max) * 100}%` }} />)}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------------ scientific contributions
interface Cand { id: string; category: string; status: string; ra: number; dec: number; summary: { n_contributions?: number; consensus_label?: string | null }; agreement: { reviews: number; agreement: number | null; agreement_lower_95: number | null }; scientific_reviews: { id: string; outcome: string; reviewed_at: string; simulated: number; organisation: string; external_ref: string | null }[] }
const STAGES = ["collecting", "community_review", "evidence_sufficient", "awaiting_scientific_review", "scientifically_reviewed"];
const STAGE_LABEL: Record<string, string> = { collecting: "Collecting", community_review: "Community review", evidence_sufficient: "Evidence sufficient", awaiting_scientific_review: "Awaiting scientific review", scientifically_reviewed: "Scientifically reviewed" };

export function ScientificPage() {
  const [c, setC] = useState<Cand[] | null>(null);
  useEffect(() => { gfetch<{ candidates: Cand[] }>("/scientific").then((r) => setC(r.candidates)).catch(() => setC([])); }, []);
  return (
    <>
      <PageTitle title="Scientific contributions" sub="How a candidate moves from a submission to a scientific outcome. Each step is separate: passing automated checks is not agreement, and community agreement is not scientific validation." />
      <div className="glass mb-6 flex flex-wrap items-center gap-2 rounded-2xl p-4 text-xs">
        {STAGES.map((s, k) => <span key={s} className="flex items-center gap-2"><span className="chip">{STAGE_LABEL[s]}</span>{k < STAGES.length - 1 && <span className="text-slate-600">→</span>}</span>)}
      </div>
      {c == null ? <Empty>Loading…</Empty> : c.length === 0 ? <Empty>No candidates yet. Candidates appear when an investigation passes the automated checks.</Empty> : (
        <div className="space-y-3">{c.map((x) => (
          <div key={x.id} className="glass rounded-2xl p-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="num text-xs text-slate-500">{x.id}</span>
              <span className="text-sm text-slate-100">{CATEGORY_LABEL[x.category as Category]} · answer “{x.summary.consensus_label ?? "–"}”</span>
              <span className="chip ml-auto">{STAGE_LABEL[x.status]}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-400">
              <span>{x.summary.n_contributions ?? 0} contributions</span>
              <span>{x.agreement.reviews} reviews{x.agreement.agreement != null && `, ${Math.round(x.agreement.agreement * 100)}% agree (95% lower bound ${Math.round((x.agreement.agreement_lower_95 ?? 0) * 100)}%)`}</span>
              <span className="num">RA {x.ra.toFixed(4)}°, Dec {x.dec.toFixed(4)}°</span>
            </div>
            {x.scientific_reviews.length > 0 && (
              <ul className="mt-3 space-y-1 border-t border-white/[0.06] pt-3 text-xs">{x.scientific_reviews.map((s) => (
                <li key={s.id} className="flex flex-wrap items-center gap-2">{s.simulated ? <Kind k="simulated" /> : <Kind k="real" />}<span className="text-slate-200">{s.outcome.replace(/_/g, " ")}</span><span className="text-slate-500">by {s.organisation} · {s.reviewed_at.slice(0, 10)}{s.external_ref && ` · ref ${s.external_ref}`}</span></li>
              ))}</ul>
            )}
          </div>
        ))}</div>
      )}
    </>
  );
}
