import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useGame } from "../../lib/game";
import { Card, PageTitle } from "./ScienceLayout";

export default function LoginPage() {
  const { login, register } = useGame();
  const [mode, setMode] = useState<"in" | "up">("in");
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [d, setD] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const [qs] = useSearchParams();

  const go = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      if (mode === "in") await login(u, p); else await register(u, p, d);
      const next = qs.get("next");
      nav(next && next.startsWith("/science") && !next.startsWith("/science/login") ? next : "/science");
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="mx-auto max-w-md">
      <PageTitle title={mode === "in" ? "Sign in" : "Create an investigator account"}
        sub="Your account keeps your investigations, reviews, streak and achievements. Only a username and password are needed; no email." />
      <Card>
        <div className="seg mb-4" role="tablist">
          <button aria-pressed={mode === "in"} onClick={() => setMode("in")}>Sign in</button>
          <button aria-pressed={mode === "up"} onClick={() => setMode("up")}>Create account</button>
        </div>
        <form onSubmit={go} className="space-y-3">
          <label className="block text-xs text-slate-400">Username
            <input className="field mt-1" value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" required minLength={3} maxLength={32} pattern="[A-Za-z0-9_.\-]+" />
          </label>
          {mode === "up" && (
            <label className="block text-xs text-slate-400">Display name <span className="text-slate-600">(optional, shown on leaderboards)</span>
              <input className="field mt-1" value={d} onChange={(e) => setD(e.target.value)} maxLength={60} />
            </label>
          )}
          <label className="block text-xs text-slate-400">Password <span className="text-slate-600">{mode === "up" && "(at least 8 characters)"}</span>
            <input className="field mt-1" type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete={mode === "in" ? "current-password" : "new-password"} required minLength={8} />
          </label>
          {err && <p className="rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">{err}</p>}
          <button className="btn btn-primary w-full py-2" disabled={busy}>{busy ? "…" : mode === "in" ? "Sign in" : "Create account"}</button>
        </form>
      </Card>
    </div>
  );
}
