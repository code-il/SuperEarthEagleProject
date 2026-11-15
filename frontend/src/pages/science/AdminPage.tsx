/** Administration: metrics, users, moderation, candidates and external review, configuration, integrations, audit. */
import { useEffect, useState } from "react";
import { gfetch, post, useGame } from "../../lib/game";
import { Card, Empty, Kind, PageTitle } from "./ScienceLayout";

type Tab = "overview" | "users" | "moderation" | "review" | "config" | "integrations" | "audit";
const TABS: [Tab, string][] = [["overview", "Overview"], ["users", "Users"], ["moderation", "Moderation"], ["review", "External review"], ["config", "Scoring rules"], ["integrations", "Integrations"], ["audit", "Audit log"]];

export default function AdminPage() {
  const { me } = useGame();
  const [tab, setTab] = useState<Tab>("overview");
  if (!me || me.role !== "admin") return <Empty>Administrators only.</Empty>;
  return (
    <>
      <PageTitle title="Administration" sub="Every change here is recorded in the audit log." />
      <div className="seg mb-5 overflow-x-auto">{TABS.map(([t, l]) => <button key={t} aria-pressed={tab === t} onClick={() => setTab(t)} className="whitespace-nowrap">{l}</button>)}</div>
      {tab === "overview" && <Overview />}{tab === "users" && <Users />}{tab === "moderation" && <Moderation />}
      {tab === "review" && <ExternalReview />}{tab === "config" && <Config />}{tab === "integrations" && <Integrations />}{tab === "audit" && <Audit />}
    </>
  );
}

function useLoad<T>(path: string, dep: unknown = 0): [T | null, () => void] {
  const [d, setD] = useState<T | null>(null);
  const load = () => { gfetch<T>(path).then(setD).catch(() => {}); };
  useEffect(load, [path, dep]); // eslint-disable-line react-hooks/exhaustive-deps
  return [d, load];
}
const Json = ({ v }: { v: unknown }) => <pre className="num max-h-80 overflow-auto whitespace-pre-wrap rounded-xl bg-black/30 p-3 text-[11px] text-slate-300">{JSON.stringify(v, null, 2)}</pre>;

function Overview() { const [m] = useLoad<Record<string, unknown>>("/admin/metrics"); return <Card title="Metrics">{m ? <Json v={m} /> : <Empty>Loading…</Empty>}</Card>; }

function Users() {
  const { toast } = useGame();
  const [q, setQ] = useState("");
  const [u, reload] = useLoad<{ users: { id: number; username: string; role: string; status: string; contribution_xp: number; created_at: string }[] }>(`/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  const set = async (id: number, body: object) => { try { await gfetch(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }); reload(); } catch (e) { toast({ tone: "warn", title: (e as Error).message }); } };
  return (
    <Card title="Users" actions={<input className="field w-48 py-1 text-xs" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />}>
      {!u ? <Empty>Loading…</Empty> : (
        <table className="w-full text-sm"><tbody>{u.users.map((x) => (
          <tr key={x.id} className="border-t border-white/[0.05]">
            <td className="py-2 text-slate-100">{x.username}</td><td className="num py-2 text-xs text-slate-500">{x.created_at.slice(0, 10)}</td><td className="num py-2 text-right text-xs">{x.contribution_xp}</td>
            <td className="py-2 pl-3"><select className="field w-auto py-1 text-xs" value={x.role} onChange={(e) => set(x.id, { role: e.target.value })}>{["player", "reviewer", "admin"].map((r) => <option key={r}>{r}</option>)}</select></td>
            <td className="py-2 pl-2"><button className="btn text-xs" onClick={() => set(x.id, { status: x.status === "active" ? "suspended" : "active" })}>{x.status === "active" ? "Suspend" : "Reinstate"}</button></td>
          </tr>
        ))}</tbody></table>
      )}
    </Card>
  );
}

function Moderation() {
  const { toast } = useGame();
  const [c, reload] = useLoad<{ contributions: { id: string; username: string; category: string; qc_status: string; hidden: number; submitted_at: string }[] }>("/admin/contributions");
  const act = async (id: string, action: string) => {
    const reason = window.prompt(`Reason for "${action}" (recorded in the audit log)`);
    if (!reason) return;
    try { await post(`/admin/contributions/${id}/moderate`, { action, reason }); reload(); } catch (e) { toast({ tone: "warn", title: (e as Error).message }); }
  };
  return (
    <Card title="Recent contributions">
      {!c ? <Empty>Loading…</Empty> : c.contributions.length === 0 ? <Empty>None yet.</Empty> : (
        <table className="w-full text-sm"><tbody>{c.contributions.map((x) => (
          <tr key={x.id} className={`border-t border-white/[0.05] ${x.hidden ? "opacity-50" : ""}`}>
            <td className="num py-2 text-xs text-slate-500">{x.id}</td><td className="py-2 text-slate-200">{x.username}</td><td className="py-2 text-xs text-slate-400">{x.category}</td><td className="py-2 text-xs">{x.qc_status}</td>
            <td className="space-x-1 py-2 text-right"><button className="btn text-xs" onClick={() => act(x.id, x.hidden ? "unhide" : "hide")}>{x.hidden ? "Unhide" : "Hide"}</button><button className="btn text-xs" onClick={() => act(x.id, "reject")}>Reject</button></td>
          </tr>
        ))}</tbody></table>
      )}
    </Card>
  );
}

function ExternalReview() {
  const { toast } = useGame();
  const [orgs, reloadOrgs] = useLoad<{ orgs: { id: number; name: string; identifier: string | null }[] }>("/admin/orgs");
  const [cands, reloadC] = useLoad<{ candidates: { id: string; category: string; status: string; n_reviews: number; n_contributions: number; summary: { consensus_label?: string } }[] }>("/admin/candidates");
  const [org, setOrg] = useState({ name: "", identifier: "" });
  const [rec, setRec] = useState({ candidate_id: "", org_id: "", outcome: "expert_classification", reviewer_identifier: "", external_ref: "", comments: "", simulated: true });
  const err = (e: unknown) => toast({ tone: "warn", title: (e as Error).message });
  return (
    <div className="space-y-6">
      <Card title="Review organisations">
        <ul className="mb-3 space-y-1 text-sm">{orgs?.orgs.map((o) => <li key={o.id} className="text-slate-200">{o.name} <span className="text-xs text-slate-500">{o.identifier}</span></li>)}</ul>
        <div className="flex gap-2"><input className="field" placeholder="Organisation name" value={org.name} onChange={(e) => setOrg({ ...org, name: e.target.value })} /><input className="field w-40" placeholder="Identifier" value={org.identifier} onChange={(e) => setOrg({ ...org, identifier: e.target.value })} />
          <button className="btn" onClick={async () => { try { await post("/admin/orgs", org); setOrg({ name: "", identifier: "" }); reloadOrgs(); } catch (e) { err(e); } }}>Add</button></div>
      </Card>
      <Card title="Candidates">
        {!cands ? <Empty>Loading…</Empty> : cands.candidates.length === 0 ? <Empty>No candidates yet.</Empty> : (
          <table className="w-full text-sm"><tbody>{cands.candidates.map((c) => (
            <tr key={c.id} className="border-t border-white/[0.05]">
              <td className="num py-2 text-xs text-slate-400">{c.id}</td><td className="py-2 text-xs">{c.category} · “{c.summary.consensus_label ?? "–"}”</td><td className="py-2 text-xs text-slate-400">{c.status.replace(/_/g, " ")}</td>
              <td className="num py-2 text-xs text-slate-500">{c.n_contributions} / {c.n_reviews}</td>
              <td className="space-x-1 py-2 text-right">
                <a className="btn text-xs" href={`/api/game/admin/export/${c.id}`} onClick={async (e) => { e.preventDefault(); try { const t = localStorage.getItem("spherex.game.token"); const r = await fetch(`/api/game/admin/export/${c.id}`, { headers: { Authorization: `Bearer ${t}` } }); const b = await r.blob(); const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = `${c.id}.json`; a.click(); reloadC(); } catch (x) { err(x); } }}>Export package</a>
                <button className="btn text-xs" onClick={() => setRec({ ...rec, candidate_id: c.id })}>Record review</button>
              </td>
            </tr>
          ))}</tbody></table>
        )}
        <p className="mt-3 text-[11px] text-slate-500">Export downloads a checksummed evidence package for you to hand to a review partner. Nothing is sent automatically.</p>
      </Card>
      <Card title="Record an external scientific review">
        <div className="grid gap-3 md:grid-cols-2">
          <input className="field" placeholder="Candidate id" value={rec.candidate_id} onChange={(e) => setRec({ ...rec, candidate_id: e.target.value })} />
          <select className="field" value={rec.org_id} onChange={(e) => setRec({ ...rec, org_id: e.target.value })}><option value="">Organisation…</option>{orgs?.orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select>
          <select className="field" value={rec.outcome} onChange={(e) => setRec({ ...rec, outcome: e.target.value })}>{["expert_classification", "accepted_candidate", "known_object_confirmed", "recognised", "not_validated"].map((o) => <option key={o} value={o}>{o.replace(/_/g, " ")}</option>)}</select>
          <input className="field" placeholder="Reviewer identifier" value={rec.reviewer_identifier} onChange={(e) => setRec({ ...rec, reviewer_identifier: e.target.value })} />
          <input className="field" placeholder="External reference (report / ticket id)" value={rec.external_ref} onChange={(e) => setRec({ ...rec, external_ref: e.target.value })} />
          <input className="field" placeholder="Comments" value={rec.comments} onChange={(e) => setRec({ ...rec, comments: e.target.value })} />
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={rec.simulated} onChange={(e) => setRec({ ...rec, simulated: e.target.checked })} /> This is simulated demonstration data <Kind k="simulated" /></label>
        <button className="btn btn-primary mt-3" onClick={async () => { try { await post("/admin/scientific-reviews", { ...rec, org_id: Number(rec.org_id) }); toast({ tone: "info", title: "Review recorded" }); reloadC(); } catch (e) { err(e); } }}>Record</button>
      </Card>
    </div>
  );
}

function Config() {
  const { toast } = useGame();
  const [cfg, reload] = useLoad<Record<string, Record<string, unknown>>>("/admin/config");
  const [key, setKey] = useState("scoring");
  const [text, setText] = useState("");
  useEffect(() => { if (cfg) setText(JSON.stringify(cfg[key], null, 2)); }, [cfg, key]);
  if (!cfg) return <Empty>Loading…</Empty>;
  return (
    <Card title="Scoring rules and thresholds" actions={<select className="field w-auto py-1 text-xs" value={key} onChange={(e) => setKey(e.target.value)}>{Object.keys(cfg).map((k) => <option key={k}>{k}</option>)}</select>}>
      <textarea className="field num min-h-[320px] text-xs" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      <button className="btn btn-primary mt-3" onClick={async () => { try { await gfetch(`/admin/config/${key}`, { method: "PUT", body: text }); toast({ tone: "info", title: "Saved" }); reload(); } catch (e) { toast({ tone: "warn", title: (e as Error).message }); } }}>Save</button>
    </Card>
  );
}

function Integrations() {
  const { toast } = useGame();
  const [d, reload] = useLoad<{ integrations: { key: string; kind: string; enabled: boolean; spec: Record<string, unknown> }[] }>("/admin/integrations");
  return (
    <Card title="External integrations (all off by default)">
      {!d ? <Empty>Loading…</Empty> : (
        <ul className="space-y-3">{d.integrations.map((x) => (
          <li key={x.key} className="rounded-xl border border-white/[0.06] p-3">
            <div className="flex items-center justify-between"><span className="text-sm text-slate-100">{x.key} <span className="text-xs text-slate-500">{x.kind}</span></span>
              <button className={`btn text-xs ${x.enabled ? "chip-on" : ""}`} onClick={async () => { try { await gfetch(`/admin/integrations/${x.key}`, { method: "PUT", body: JSON.stringify({ enabled: !x.enabled }) }); reload(); } catch (e) { toast({ tone: "warn", title: (e as Error).message }); } }}>{x.enabled ? "Enabled" : "Disabled"}</button></div>
            <p className="mt-1 text-xs text-slate-400">{String(x.spec.description ?? "")}</p>
          </li>
        ))}</ul>
      )}
      <p className="mt-3 text-[11px] text-slate-500">No NASA or IPAC endpoint is assumed. A webhook or API client needs an https URL and secret set by an administrator before it can be enabled.</p>
    </Card>
  );
}

function Audit() { const [a] = useLoad<{ audit: { id: number; at: string; actor_id: number | null; action: string; target_type: string | null; target_id: string | null; details: unknown }[] }>("/admin/audit"); return (
  <Card title="Audit log">{!a ? <Empty>Loading…</Empty> : (
    <table className="w-full text-xs"><tbody>{a.audit.map((x) => (
      <tr key={x.id} className="border-t border-white/[0.05] align-top"><td className="num py-1.5 pr-3 text-slate-500">{x.at.replace("T", " ").slice(0, 19)}</td><td className="py-1.5 pr-3 text-slate-200">{x.action}</td><td className="num py-1.5 pr-3 text-slate-400">{x.target_type} {x.target_id}</td><td className="num py-1.5 text-slate-500">{JSON.stringify(x.details).slice(0, 120)}</td></tr>
    ))}</tbody></table>
  )}</Card>
); }
