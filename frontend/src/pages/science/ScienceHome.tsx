/** "Explore": today's challenges, where help is needed, and the player's standing. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CATEGORY_HINT, CATEGORY_LABEL, fmtPts, gfetch, useGame, type Category, type Investigation } from "../../lib/game";
import { Card, PageTitle, Stat } from "./ScienceLayout";

interface Challenge { day: string; key: string; title: string; description: string; reward_xp: number; completed?: boolean }
interface Progress { totals: Record<string, number | null> }

export default function ScienceHome() {
  const { me } = useGame();
  const [ch, setCh] = useState<Challenge[] | null>(null);
  const [inv, setInv] = useState<Investigation[] | null>(null);
  const [pr, setPr] = useState<Progress | null>(null);
  useEffect(() => {
    gfetch<{ challenges: Challenge[] }>("/challenges").then((r) => setCh(r.challenges)).catch(() => setCh([]));
    gfetch<{ investigations: Investigation[] }>("/investigations").then((r) => setInv(r.investigations)).catch(() => setInv([]));
    gfetch<Progress>("/progress").then(setPr).catch(() => {});
  }, [me?.id]);
  const by = (c: Category) => inv?.filter((i) => i.category === c) ?? [];
  const t = pr?.totals ?? {};

  return (
    <>
      <PageTitle title={me ? `Welcome back, ${me.display_name || me.username}` : "Help chart the infrared sky"}
        sub="Every task here points at a real source in the Atlas and the real SPHEREx images of it. Your work is checked automatically, then reviewed blind by other investigators. Results that an external scientific team accepts are recorded separately as validated." />

      {me && (
        <div className="glass mb-6 grid grid-cols-2 gap-4 rounded-2xl p-5 sm:grid-cols-4">
          <Stat k="Contribution points" v={fmtPts(me.contribution_xp)} hint="From investigations that passed the automated checks and from reviews" />
          <Stat k="Level" v={`${me.level.level} · ${me.level.title}`} />
          <Stat k="Login streak" v={`${me.streak.current} days`} hint={`Longest ${me.streak.longest} days`} />
          <Stat k="Global rank" v={me.ranks.all ? `#${me.ranks.all}` : "unranked"} />
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          <Card title="Where help is needed">
            <div className="grid gap-3 sm:grid-cols-2">
              {(Object.keys(CATEGORY_LABEL) as Category[]).map((c) => {
                const list = by(c);
                const real = list.filter((i) => i.kind === "real").length;
                return (
                  <Link key={c} to={c === "verification" ? "/science/review" : `/science/investigate?category=${c}`}
                    className="group rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 transition hover:border-white/15 hover:bg-white/[0.05]">
                    <div className="flex items-center justify-between">
                      <span className="text-[14px] font-semibold text-slate-100">{CATEGORY_LABEL[c]}</span>
                      <span className="num text-[11px] text-slate-500">{inv == null ? "…" : `${real} open`}</span>
                    </div>
                    <p className="mt-1 text-xs leading-relaxed text-slate-400">{CATEGORY_HINT[c]}</p>
                    <span className="mt-2 inline-block text-xs text-sky-200 opacity-0 transition group-hover:opacity-100">Start →</span>
                  </Link>
                );
              })}
            </div>
          </Card>
          <Card title="Recommended for you" actions={<Link to="/science/investigate" className="text-xs text-sky-200 hover:text-white">All investigations →</Link>}>
            {inv == null ? <p className="text-sm text-slate-500">Loading…</p> : (
              <ul className="divide-y divide-white/[0.05]">
                {inv.slice(0, 6).map((i) => (
                  <li key={i.id}>
                    <Link to={i.category === "verification" ? `/science/review/${i.candidate_id}` : `/science/investigate/${i.id}`} className="flex items-center gap-3 py-2.5 hover:text-white">
                      <span className="w-28 shrink-0 text-xs text-slate-500">{CATEGORY_LABEL[i.category]}</span>
                      <span className="min-w-0 flex-1 truncate text-sm text-slate-200">{i.source_name}</span>
                      {i.data.class_source === "none" && <span className="chip border-fuchsia-300/30 py-0 text-[10.5px] text-fuchsia-200">Uncharted</span>}
                      {i.kind === "training" && <span className="chip border-sky-300/30 py-0 text-[10.5px] text-sky-200">Training</span>}
                      <span className="num text-[11px] text-slate-500">{"●".repeat(i.difficulty)}<span className="text-slate-700">{"●".repeat(3 - Math.min(3, i.difficulty))}</span></span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
        <div className="space-y-6">
          <Card title="Today's challenges">
            {ch == null ? <p className="text-sm text-slate-500">Loading…</p> : ch.length === 0 ? <p className="text-sm text-slate-400">No challenges today: no matching tasks are open.</p> : (
              <ul className="space-y-3">
                {ch.map((c) => (
                  <li key={c.key} className="flex gap-3">
                    <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] ${c.completed ? "border-emerald-300/50 bg-emerald-400/15 text-emerald-200" : "border-white/15 text-slate-500"}`}>{c.completed ? "✓" : ""}</span>
                    <div><div className="text-sm text-slate-100">{c.title} <span className="num text-[11px] text-slate-500">+{c.reward_xp} XP</span></div><div className="text-xs text-slate-400">{c.description}</div></div>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-4 text-[11px] text-slate-500">Challenges complete only when a matching investigation passes the automated checks. A login alone never completes one.</p>
          </Card>
          <Card title="The community so far" actions={<Link to="/science/progress" className="text-xs text-sky-200 hover:text-white">Details →</Link>}>
            <dl className="grid grid-cols-2 gap-4">
              <Stat k="Investigations" v={fmtPts(t.investigations_completed as number)} />
              <Stat k="Reviews" v={fmtPts(t.independent_reviews as number)} />
              <Stat k="Uncharted classified" v={fmtPts(t.uncharted_sources_classified as number)} />
              <Stat k="Still uncharted" v={fmtPts(t.uncharted_sources_in_atlas as number)} hint="Atlas sources with no class in any catalogue" />
            </dl>
          </Card>
        </div>
      </div>
    </>
  );
}
