/** My profile (editable) and a public player profile. */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CATEGORY_LABEL, fmtPts, gfetch, statusLabel, statusTone, useGame, type Achievement, type Category, type Profile } from "../../lib/game";
import { Card, Empty, NeedSignIn, PageTitle, Stat } from "./ScienceLayout";

interface Contribution { id: string; category: Category; source_name: string; submitted_at: string; qc_status: string; science_status: string; candidate_status: string | null; points: number | null; reviews: number; training: number; investigation_id: string }
const RARITY: Record<string, string> = { common: "text-slate-300", rare: "text-sky-200", epic: "text-violet-200", legendary: "text-amber-200" };

export function MyProfile() {
  const { me, refresh, toast } = useGame();
  const [all, setAll] = useState<Achievement[]>([]);
  const [mine, setMine] = useState<Contribution[] | null>(null);
  const [edit, setEdit] = useState(false);
  const [form, setForm] = useState({ display_name: "", bio: "", share_attribution: false, specialisations: [] as Category[] });
  useEffect(() => {
    if (!me) return;
    gfetch<{ achievements: Achievement[] }>("/achievements").then((r) => setAll(r.achievements)).catch(() => {});
    gfetch<{ contributions: Contribution[] }>("/contributions/mine").then((r) => setMine(r.contributions)).catch(() => setMine([]));
    setForm({ display_name: me.display_name ?? "", bio: me.bio ?? "", share_attribution: !!me.share_attribution, specialisations: me.specialisations });
  }, [me]);
  if (!me) return <NeedSignIn what="see your profile" />;
  const save = async () => {
    try { await gfetch("/me", { method: "PATCH", body: JSON.stringify(form) }); await refresh(); setEdit(false); toast({ tone: "info", title: "Profile saved" }); }
    catch (e) { toast({ tone: "warn", title: "Could not save", body: (e as Error).message }); }
  };
  return (
    <>
      <PageTitle title={me.display_name || me.username} sub={me.bio || `Investigator since ${me.created_at.slice(0, 10)}`} right={<button className="btn" onClick={() => setEdit(!edit)}>{edit ? "Cancel" : "Edit profile"}</button>} />
      {edit && (
        <Card title="Edit profile" className="mb-6">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block text-xs text-slate-400">Display name<input className="field mt-1" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} maxLength={60} /></label>
            <label className="block text-xs text-slate-400">Bio<input className="field mt-1" value={form.bio} onChange={(e) => setForm({ ...form, bio: e.target.value })} maxLength={500} /></label>
          </div>
          <div className="mt-4 text-xs text-slate-400">Interests
            <div className="mt-1.5 flex flex-wrap gap-1.5">{(Object.keys(CATEGORY_LABEL) as Category[]).map((c) => (
              <button key={c} className={`chip ${form.specialisations.includes(c) ? "chip-on" : ""}`} onClick={() => setForm({ ...form, specialisations: form.specialisations.includes(c) ? form.specialisations.filter((x) => x !== c) : [...form.specialisations, c] })}>{CATEGORY_LABEL[c]}</button>
            ))}</div></div>
          <label className="mt-4 flex items-start gap-2 text-xs text-slate-300"><input type="checkbox" className="mt-0.5" checked={form.share_attribution} onChange={(e) => setForm({ ...form, share_attribution: e.target.checked })} />
            <span>Credit me by username in evidence packages sent to scientific reviewers. Off: you appear as <span className="num">{me.pseudonym}</span>.</span></label>
          <button className="btn btn-primary mt-4" onClick={save}>Save</button>
        </Card>
      )}
      <ProfileBody p={me} all={all} />
      <Card title="My contributions" className="mt-6">
        {mine == null ? <Empty>Loading…</Empty> : mine.length === 0 ? <Empty>No submissions yet. <Link className="text-sky-200" to="/science/investigate">Start an investigation</Link>.</Empty> : (
          <div className="overflow-x-auto"><table className="w-full text-sm">
            <thead><tr className="text-left text-[11px] text-slate-500"><th className="pb-2 pr-3 font-normal">Submitted</th><th className="pb-2 pr-3 font-normal">Source</th><th className="pb-2 pr-3 font-normal">Checks</th><th className="pb-2 pr-3 font-normal">Community</th><th className="pb-2 pr-3 font-normal">Science</th><th className="pb-2 text-right font-normal">Points</th></tr></thead>
            <tbody>{mine.map((c) => (
              <tr key={c.id} className="border-t border-white/[0.05]">
                <td className="num py-2 pr-3 text-xs text-slate-400">{c.submitted_at.slice(0, 10)}</td>
                <td className="py-2 pr-3"><Link className="text-slate-100 hover:text-white" to={`/science/investigate/${c.investigation_id}`}>{c.source_name}</Link><div className="text-[11px] text-slate-500">{CATEGORY_LABEL[c.category]}{c.training ? " · training" : ""}</div></td>
                <td className="py-2 pr-3"><span className={`chip py-0 ${statusTone[c.qc_status]}`}>{statusLabel(c.qc_status)}</span></td>
                <td className="py-2 pr-3 text-xs text-slate-400">{c.candidate_status ? `${statusLabel(c.candidate_status)} · ${c.reviews} reviews` : "–"}</td>
                <td className="py-2 pr-3 text-xs text-slate-400">{statusLabel(c.science_status)}</td>
                <td className="num py-2 text-right text-slate-200">{fmtPts(c.points ?? 0)}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </>
  );
}

export function PlayerProfile() {
  const { id } = useParams();
  const [p, setP] = useState<Profile | null>(null);
  const [all, setAll] = useState<Achievement[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    gfetch<Profile>(`/players/${id}`).then(setP).catch((e) => setErr(e.message));
    gfetch<{ achievements: Achievement[] }>("/achievements").then((r) => setAll(r.achievements)).catch(() => {});
  }, [id]);
  if (err) return <Empty>{err}</Empty>;
  if (!p) return <Empty>Loading…</Empty>;
  return <><PageTitle title={p.display_name || p.username} sub={p.bio || `Investigator since ${p.created_at.slice(0, 10)}`} /><ProfileBody p={p} all={all.map((a) => ({ ...a, unlocked_at: p.achievements.find((x) => x.key === a.key)?.unlocked_at ?? null }))} /></>;
}

function ProfileBody({ p, all }: { p: Profile; all: Achievement[] }) {
  const s = p.stats;
  const lv = p.level;
  return (
    <div className="space-y-6">
      <div className="glass grid grid-cols-2 gap-5 rounded-2xl p-5 md:grid-cols-4">
        <Stat k="Contribution points" v={fmtPts(p.contribution_xp)} hint="Global Contribution Leaderboard" />
        <Stat k="Participation XP" v={fmtPts(p.participation_xp)} hint="Logins, streaks, challenges, training: counts toward level only" />
        <Stat k="Rank all-time / week" v={`${p.ranks.all ? `#${p.ranks.all}` : "–"} / ${p.ranks.week ? `#${p.ranks.week}` : "–"}`} />
        <div><dt className="text-[11px] text-slate-500">Level {lv.level} · {lv.title}</dt>
          <dd className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${(lv.progress ?? 1) * 100}%` }} /></dd>
          <dd className="num mt-1 text-[11px] text-slate-500">{fmtPts(p.total_xp)}{lv.next_level_at != null && ` / ${lv.next_level_at.toLocaleString()}`} XP</dd></div>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Record">
          <dl className="grid grid-cols-2 gap-4">
            <Stat k="Investigations completed" v={s.investigations_completed} />
            <Stat k="Valid submissions" v={s.valid_submissions} />
            <Stat k="Reviews given" v={s.reviews_given} />
            <Stat k="Independently reviewed" v={s.contributions_independently_reviewed} />
            <Stat k="Training completed" v={s.training_completed} />
            <Stat k="Scientifically recognised" v={s.scientifically_recognised} hint="Accepted by an external scientific review" />
          </dl>
          {p.derived_specialisations.length > 0 && <div className="mt-4 flex flex-wrap gap-1.5">{p.derived_specialisations.map((d) => <span key={d.category} className="chip">{CATEGORY_LABEL[d.category]} · {d.count}</span>)}</div>}
        </Card>
        <Card title="Login streak">
          <div className="flex items-baseline gap-6">
            <div><div className="num text-3xl text-white">{p.streak.current}</div><div className="text-[11px] text-slate-500">current (days)</div></div>
            <div><div className="num text-xl text-slate-300">{p.streak.longest}</div><div className="text-[11px] text-slate-500">longest</div></div>
            <div><div className="num text-xl text-slate-300">{p.streak.total_active_days}</div><div className="text-[11px] text-slate-500">active days</div></div>
          </div>
          <div className="mt-4 flex gap-1.5">{[3, 7, 14, 30, 60, 100].map((m) => <span key={m} className={`chip ${p.streak.milestones.includes(m) ? "chip-on" : ""}`}>{m}d</span>)}</div>
          <p className="mt-3 text-[11px] text-slate-500">Days follow your own time zone. Login XP raises your level but never the contribution leaderboard.</p>
        </Card>
      </div>
      <Card title="Achievements">
        {all.length === 0 ? <Empty>Loading…</Empty> : (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{all.map((a) => (
            <div key={a.key} className={`rounded-xl border p-3 ${a.unlocked_at ? "border-white/10 bg-white/[0.04]" : "border-white/[0.05] opacity-45"}`}>
              <div className="flex items-center justify-between gap-2"><span className={`text-sm font-medium ${RARITY[a.rarity] ?? ""}`}>{a.name}</span><span className="text-[10px] uppercase tracking-wide text-slate-500">{a.kind === "scientific" ? "scientific" : a.rarity}</span></div>
              <p className="mt-0.5 text-xs text-slate-400">{a.description}</p>
              {a.unlocked_at && <p className="num mt-1 text-[10.5px] text-slate-500">{a.unlocked_at.slice(0, 10)}</p>}
            </div>
          ))}</div>
        )}
      </Card>
    </div>
  );
}
