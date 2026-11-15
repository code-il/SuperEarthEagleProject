/**
 * The Daily Survey: the same scored stars for everyone, mixed with uncharted sources picked to
 * cover as much sky as possible. Which targets are scored is revealed only at the end.
 * Everything here (targets, grades, ranks) comes from the server.
 */
import { useCallback, useEffect, useState } from "react";
import { gfetch, post, statusLabel, statusTone, useGame, type Obs } from "../../lib/game";
import { DemoTag } from "./BoardsPages";
import { CLASSES, ObsPicker } from "./InvestigatePage";
import { Card, Empty, NeedSignIn, PageTitle, Stat } from "./ScienceLayout";

type Grade = "exact" | "near" | "miss" | "skipped";
interface DailyItem {
  slot: number; label: string; bonus: boolean; status: "open" | "answered" | "skipped"; ra: number; dec: number; data_status: string;
  observations: Obs[] | null; observation_count: number | null; my_label: string | null; qc_status: string | null;
  anchor?: boolean; grade?: Grade; answer?: string; anchor_slot?: number;
}
interface Run {
  started_at: string; finished_at: string | null; surveyed: number; items: DailyItem[];
  score?: number; max_score?: number; tiles?: string; rank?: number | null; ranked?: number; contribution_points?: number; participation_xp?: number; share?: string;
}
interface Community { players_today: number; coordinates_today: number; sources_with_enough_votes: number; uncharted_open: number; votes_wanted: number }
interface DailyView {
  day: string; number: number; community: Community; run: Run | null; streak?: number;
  reliability?: { anchors_answered: number; exact: number; near: number; score: number };
}
interface BoardEntry { rank: number; user_id: number; name: string; score: number; max_score: number; surveyed: number; tiles: string; me: boolean; demo?: boolean }
interface Board { total: number; entries: BoardEntry[]; me: BoardEntry | null; room_name: string | null; includes_demo?: boolean }
interface Room { code: string; name: string; members: number; owner: boolean }
interface AnswerResult { accepted: boolean; qc_status: string; missing?: string[]; errors?: string[]; warnings?: string[]; finished?: boolean }

const CLASS_NAME = Object.fromEntries(CLASSES);
const TILE: Record<Grade, string> = { exact: "bg-emerald-400/80", near: "bg-amber-300/80", miss: "bg-slate-600", skipped: "bg-slate-800 border border-white/10" };
const GRADE_TEXT: Record<Grade, string> = { exact: "Exact", near: "Close (neighbouring class)", miss: "Miss", skipped: "Skipped" };

export default function DailyPage() {
  const { me, refresh, toast } = useGame();
  const [v, setV] = useState<DailyView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [slot, setSlot] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [boardKey, setBoardKey] = useState(0);

  const load = useCallback(async () => {
    try {
      const x = await gfetch<DailyView>("/daily");
      setV(x);
      const open = x.run?.items.find((i) => i.status === "open");
      setSlot((s) => (s != null && x.run?.items.some((i) => i.slot === s && i.status === "open") ? s : open?.slot ?? null));
      return x;
    } catch (e) { setErr((e as Error).message); return null; }
  }, []);
  useEffect(() => { load(); }, [load, me?.id]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); await load(); setBoardKey((k) => k + 1); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  if (!v) return err ? <Empty>{err}</Empty> : <Empty>Loading today's survey…</Empty>;
  const run = v.run;
  const done = !!run?.finished_at;
  const main = run?.items.filter((i) => !i.bonus) ?? [];
  const bonus = run?.items.filter((i) => i.bonus) ?? [];
  const current = run?.items.find((i) => i.slot === slot && i.status === "open") ?? null;

  return (
    <>
      <PageTitle title={`Daily Survey #${v.number}`}
        sub="Everyone gets the same few catalogued stars to classify; your rank depends on how many you get right. Mixed in with them are real sources no catalogue has classified, picked so the community covers as much new sky as possible. You won't know which targets were scored until the end, so give each one the same care." />
      {err && <p className="mb-4 rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">{err}</p>}

      <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          {!me ? <NeedSignIn what="play today's survey" /> : !run ? (
            <Card title="Today's survey">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <p className="max-w-lg text-sm leading-relaxed text-slate-300">
                  You'll get several targets. For each one, open its SPHEREx images, look at how bright it is at each wavelength, and choose a class. Some targets are well-known stars that everyone scores on. The rest are uncharted, and your answers go to the community's classification of them.</p>
                <button className="btn btn-primary px-5" disabled={busy} onClick={() => act(() => post("/daily/start", {}))}>{busy ? "Preparing…" : "Start today's survey"}</button>
              </div>
            </Card>
          ) : (
            <>
              <Card title={done ? "Today's targets" : `Targets: ${main.filter((i) => i.status !== "open").length} of ${main.length} done`}>
                <div className="flex flex-wrap gap-2">
                  {main.map((i) => (
                    <button key={i.slot} onClick={() => i.status === "open" && setSlot(i.slot)} disabled={i.status !== "open"}
                      className={`flex h-11 w-11 items-center justify-center rounded-lg text-xs font-semibold transition ${
                        done && i.anchor && i.grade ? `${TILE[i.grade]} text-slate-950` :
                        i.slot === slot && !done ? "border border-sky-300/60 bg-sky-400/15 text-sky-100" :
                        i.status === "open" ? "border border-white/15 text-slate-300 hover:border-white/30" :
                        i.status === "skipped" ? "border border-white/5 text-slate-600" : "bg-white/10 text-slate-300"}`}
                      title={done ? (i.anchor ? `Scored star · ${GRADE_TEXT[i.grade!]}` : "Uncharted source") : i.label}>
                      {done && !i.anchor ? "🔭" : i.slot + 1}
                    </button>
                  ))}
                </div>
                {done && <p className="mt-3 text-[11px] text-slate-500">Coloured: scored stars (green exact, yellow neighbouring class, grey miss). 🔭 uncharted sources: your answer is now part of the survey.</p>}
              </Card>

              {current && <TargetPanel key={current.slot} item={current} busy={busy}
                onAnswer={(r) => {
                  if (!r.accepted) return;
                  toast({ tone: r.qc_status.startsWith("passed") ? "good" : "warn", title: `${current.label} recorded`, body: statusLabel(r.qc_status) });
                  refresh(); load(); setBoardKey((k) => k + 1);
                }}
                onSkip={() => act(() => post(`/daily/items/${current.slot}/skip`, {}))} />}

              {done && run && <Results v={v} run={run} />}

              {done && (
                <Card title="Keep surveying" actions={<span className="text-xs text-slate-500">{bonus.filter((i) => i.status === "answered").length} extra today</span>}>
                  <p className="mb-3 text-sm text-slate-400">
                    Each extra target is an uncharted source that hasn't had {v.community.votes_wanted} independent classifications yet. Your daily score won't change. Every so often a hidden catalogued star checks your accuracy, and a higher accuracy gives your classifications more weight in the consensus.</p>
                  {!current && <button className="btn btn-primary" disabled={busy} onClick={() => act(async () => { await post("/daily/bonus", {}); })}>{busy ? "Finding sky…" : "Give me another target"}</button>}
                  {bonus.some((i) => i.status !== "open" && i.anchor) && (
                    <ul className="mt-3 space-y-1 text-xs text-slate-400">
                      {bonus.filter((i) => i.anchor && i.status !== "open").map((i) => (
                        <li key={i.slot}>Accuracy check on {i.label}: <span className="text-slate-200">{GRADE_TEXT[i.grade ?? "skipped"]}</span> (catalogue says {CLASS_NAME[i.answer ?? ""] ?? i.answer})</li>
                      ))}
                    </ul>
                  )}
                </Card>
              )}
            </>
          )}
        </div>

        <div className="space-y-6">
          <CommunityCard v={v} />
          <BoardCard key={boardKey} signedIn={!!me} />
          {me && <RoomsCard onChange={() => setBoardKey((k) => k + 1)} />}
        </div>
      </div>
    </>
  );
}

function TargetPanel({ item, busy, onAnswer, onSkip }: {
  item: DailyItem; busy: boolean; onAnswer: (r: AnswerResult) => void; onSkip: () => void;
}) {
  const [it, setIt] = useState<DailyItem | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [conf, setConf] = useState(0.6);
  const [obs, setObs] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [sending, setSending] = useState(false);
  const [res, setRes] = useState<AnswerResult | null>(null);
  const fetchItem = (refresh = false) => gfetch<DailyItem>(`/daily/items/${item.slot}${refresh ? "?refresh=true" : ""}`).then(setIt).catch((e) => setLoadErr(e.message));
  useEffect(() => { fetchItem(); }, [item.slot]); // eslint-disable-line react-hooks/exhaustive-deps

  const t = it ?? item;
  const exploreUrl = `/map?view=explore&ra=${t.ra.toFixed(5)}&dec=${t.dec.toFixed(5)}&fov=0.05&det=3`;
  const submit = async () => {
    setSending(true); setRes(null);
    try {
      const r = await post<AnswerResult>(`/daily/items/${item.slot}/answer`, { label, confidence: conf, observations: obs, notes });
      setRes(r); onAnswer(r);
    } catch (e) { setRes({ accepted: false, qc_status: "error", errors: [(e as Error).message] }); } finally { setSending(false); }
  };

  return (
    <Card title={<span className="flex items-center gap-2">{t.label}{t.bonus && <span className="chip py-0 text-[10.5px]">extra</span>}</span>}
      actions={<a href={exploreUrl} target="_blank" rel="noreferrer" className="btn btn-primary text-xs">Open SPHEREx images ↗</a>}>
      {loadErr && <p className="mb-3 text-xs text-red-300">{loadErr}</p>}
      {!it ? <p className="text-sm text-slate-400">Finding this target's SPHEREx images…</p> :
        it.observations == null ? (
          <div className="flex items-center justify-between gap-3 text-sm text-slate-400"><span>The SPHEREx archive (IRSA) didn't respond for this target.</span>
            <span className="flex gap-2"><button className="btn" onClick={() => fetchItem(true)}>Try again</button><button className="btn" disabled={busy} onClick={onSkip}>Skip</button></span></div>
        ) : (
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <p className="text-xs leading-relaxed text-slate-400">Open the images, then step through the wavelengths. Hot stars are brightest in the blue channels, cool dwarfs and brown dwarfs in the red. Compare the target with its neighbours, then choose a class.</p>
            <div><div className="mb-1.5 text-xs text-slate-400">Class</div>
              <div className="flex flex-wrap gap-1.5">{CLASSES.map(([k, l]) => <button type="button" key={k} onClick={() => setLabel(k)} className={`chip ${label === k ? "chip-on" : "hover:text-slate-200"}`}>{l}</button>)}</div></div>
            <label className="block text-xs text-slate-400">Confidence <span className="num text-slate-200">{Math.round(conf * 100)}%</span>
              <input type="range" min={0} max={1} step={0.05} value={conf} onChange={(e) => setConf(Number(e.target.value))} className="mt-1 w-full" /></label>
            {it.observations.length > 0 && <ObsPicker obs={it.observations} value={obs} onChange={setObs} />}
            <label className="block text-xs text-slate-400">Notes <span className="text-slate-600">(optional: what you saw)</span>
              <textarea className="field mt-1 min-h-[56px]" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} /></label>
            {res && !res.accepted && (
              <div className="rounded-lg border border-amber-300/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-100">
                Not recorded yet. Fix this and submit again:<ul className="mt-1 list-disc pl-4">{[...(res.missing ?? []), ...(res.errors ?? [])].map((m) => <li key={m}>{m}</li>)}</ul></div>
            )}
            {res?.accepted && <p className={`text-xs ${statusTone[res.qc_status] ?? ""}`}>Recorded · {statusLabel(res.qc_status)}</p>}
            <div className="flex items-center justify-between gap-3">
              <button type="button" className="btn" disabled={busy || sending} onClick={onSkip}>Skip</button>
              <button className="btn btn-primary px-5" disabled={!label || sending || busy}>{sending ? "Checking…" : "Submit"}</button>
            </div>
          </form>
        )}
    </Card>
  );
}

function Results({ v, run }: { v: DailyView; run: Run }) {
  const [copied, setCopied] = useState(false);
  const scored = run.items.filter((i) => !i.bonus && i.anchor).sort((a, b) => (a.anchor_slot ?? 0) - (b.anchor_slot ?? 0));
  const share = async () => {
    const text = `${run.share}\n${location.origin}/science/daily`;
    try {
      if (navigator.share) await navigator.share({ text });
      else { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000); }
    } catch { /* user cancelled */ }
  };
  return (
    <Card title={`Daily Survey #${v.number} · results`} actions={<button className="btn btn-primary text-xs" onClick={share}>{copied ? "Copied" : "Share"}</button>}>
      <div className="flex flex-wrap items-center gap-6">
        <div className="flex gap-1.5">{scored.map((i) => <div key={i.slot} className={`h-12 w-12 rounded-lg ${TILE[i.grade ?? "skipped"]}`} title={GRADE_TEXT[i.grade ?? "skipped"]} />)}</div>
        <dl className="grid flex-1 grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat k="Score" v={`${run.score}/${run.max_score}`} />
          <Stat k="Rank today" v={run.rank ? `#${run.rank} of ${run.ranked}` : "–"} />
          <Stat k="Sky surveyed" v={`${run.surveyed} coordinates`} hint="Uncharted sources you classified that passed the automated checks" />
          <Stat k="Daily streak" v={`${v.streak ?? 0} days`} />
        </dl>
      </div>
      <ul className="mt-5 space-y-1.5 text-xs">
        {scored.map((i) => (
          <li key={i.slot} className="flex flex-wrap gap-x-2 text-slate-400">
            <span className="text-slate-300">{i.label}</span>: you said <span className="text-slate-200">{i.my_label ? CLASS_NAME[i.my_label] ?? i.my_label : "nothing (skipped)"}</span>,
            the catalogue says <span className="text-slate-200">{CLASS_NAME[i.answer ?? ""] ?? i.answer}</span> · {GRADE_TEXT[i.grade ?? "skipped"]}
          </li>
        ))}
      </ul>
      <p className="mt-4 border-t border-white/[0.06] pt-3 text-[11px] text-slate-500">
        +{run.contribution_points ?? 0} contribution points from the uncharted sources, +{run.participation_xp ?? 0} XP from the scored stars.
        {v.reliability && ` Your accuracy on catalogued stars so far: ${Math.round(v.reliability.score * 100)}% (${v.reliability.anchors_answered} checked).`}
      </p>
    </Card>
  );
}

function CommunityCard({ v }: { v: DailyView }) {
  const c = v.community;
  return (
    <Card title="The survey today">
      <dl className="grid grid-cols-2 gap-4">
        <Stat k="Players" v={c.players_today} />
        <Stat k="New coordinates" v={c.coordinates_today} hint="Distinct uncharted sources classified today (passed checks)" />
        <Stat k="Sources with enough votes" v={c.sources_with_enough_votes} hint={`${c.votes_wanted} independent classifications each`} />
        <Stat k="Still need votes" v={c.uncharted_open} />
      </dl>
    </Card>
  );
}

function BoardCard({ signedIn }: { signedIn: boolean }) {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [room, setRoom] = useState<string>("");
  const [order, setOrder] = useState<"score" | "surveyed">("score");
  const [b, setB] = useState<Board | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (signedIn) gfetch<{ rooms: Room[] }>("/daily/rooms").then((r) => setRooms(r.rooms)).catch(() => {}); }, [signedIn]);
  useEffect(() => {
    setErr(null);
    const q = new URLSearchParams({ order, ...(room ? { room } : {}) });
    gfetch<Board>(`/daily/leaderboard?${q}`).then(setB).catch((e) => setErr(e.message));
  }, [room, order]);
  return (
    <Card title="Today's board">
      <div className="mb-3 space-y-2">
        {rooms.length > 0 && (
          <select className="field py-1.5 text-xs" value={room} onChange={(e) => setRoom(e.target.value)}>
            <option value="">Everyone</option>{rooms.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
          </select>
        )}
        <div className="seg"><button aria-pressed={order === "score"} onClick={() => setOrder("score")}>Score</button><button aria-pressed={order === "surveyed"} onClick={() => setOrder("surveyed")}>Sky surveyed</button></div>
      </div>
      {err ? <p className="text-xs text-red-300">{err}</p> : !b ? <p className="text-xs text-slate-500">Loading…</p> : b.entries.length === 0 ? <p className="text-xs text-slate-400">Nobody has finished today's survey yet.</p> : (
        <ol className="space-y-1 text-xs">
          {b.entries.slice(0, 15).map((e) => (
            <li key={e.user_id} className={`flex items-center gap-2 rounded-lg px-2 py-1 ${e.me ? "bg-sky-400/10" : ""}`}>
              <span className="num w-6 text-slate-500">{e.rank}</span>
              <span className="min-w-0 flex-1 truncate text-slate-200">{e.name}{e.demo && <DemoTag />}</span>
              <span className="tracking-tighter">{e.tiles}</span>
              <span className="num w-10 text-right text-slate-300">{order === "score" ? `${e.score}/${e.max_score}` : e.surveyed}</span>
            </li>
          ))}
          {b.me && b.me.rank > 15 && <li className="flex gap-2 rounded-lg bg-sky-400/10 px-2 py-1"><span className="num w-6 text-slate-500">{b.me.rank}</span><span className="flex-1 text-slate-200">You</span><span className="num">{order === "score" ? b.me.score : b.me.surveyed}</span></li>}
        </ol>
      )}
      {b && <p className="mt-2 text-[11px] text-slate-500">{b.total} finished · ties broken by sky surveyed, then finish time{b.includes_demo && " · rows marked demo are sample players"}</p>}
    </Card>
  );
}

function RoomsCard({ onChange }: { onChange: () => void }) {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => gfetch<{ rooms: Room[] }>("/daily/rooms").then((r) => setRooms(r.rooms)).catch(() => {});
  useEffect(() => { load(); }, []);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setMsg(null);
    try { await fn(); if (ok) setMsg(ok); await load(); onChange(); } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <Card title="Rooms">
      <p className="mb-3 text-xs text-slate-400">Compare scores with a class, a club or friends. Share the code; anyone with it can join.</p>
      {rooms.length > 0 && (
        <ul className="mb-3 space-y-1 text-xs">{rooms.map((r) => (
          <li key={r.code} className="flex items-center gap-2"><span className="flex-1 truncate text-slate-200">{r.name}</span>
            <span className="num rounded bg-white/[0.06] px-1.5 text-slate-300">{r.code}</span><span className="text-slate-500">{r.members}</span>
            <button className="text-slate-500 hover:text-white" onClick={() => run(() => post(`/daily/rooms/${r.code}/leave`, {}), `Left ${r.name}`)} aria-label={`Leave ${r.name}`}>✕</button></li>
        ))}</ul>
      )}
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); run(() => post("/daily/rooms/join", { code }), "Joined").then(() => setCode("")); }}>
        <input className="field num py-1.5 text-xs uppercase" placeholder="Room code" value={code} onChange={(e) => setCode(e.target.value)} maxLength={6} />
        <button className="btn shrink-0 text-xs" disabled={code.trim().length < 6}>Join</button>
      </form>
      <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); run(async () => { const r = await post<{ code: string }>("/daily/rooms", { name }); setMsg(`Created. Code: ${r.code}`); }, "").then(() => setName("")); }}>
        <input className="field py-1.5 text-xs" placeholder="New room name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
        <button className="btn shrink-0 text-xs" disabled={!name.trim()}>Create</button>
      </form>
      {msg && <p className="mt-2 text-xs text-slate-300">{msg}</p>}
    </Card>
  );
}
