/**
 * Client for the citizen-science game API (/api/game). The browser never computes points:
 * every score, status and reward shown here comes back from the server.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export type Category = "moving" | "classification" | "image_quality" | "spectral" | "verification";
export const CATEGORY_LABEL: Record<Category, string> = {
  moving: "Moving objects", classification: "Classification", image_quality: "Image quality", spectral: "Spectral", verification: "Verification",
};
export const CATEGORY_HINT: Record<Category, string> = {
  moving: "Measure a fast-moving star at two dates and test it against its Gaia proper motion.",
  classification: "Classify sources that no catalogue has a class for.",
  image_quality: "Find artefacts (ghosts, saturation, trails) in bright-star fields.",
  spectral: "Read absorption bands in a measured SPHEREx spectrum.",
  verification: "Independently review another investigator's candidate.",
};

export interface Obs { id: string; time: string | null; band: number | null; detector: number | null; wavelength_min: number | null; wavelength_max: number | null; release: string | null; mjd: number | null; quality_flags: string[] }
export interface Investigation {
  id: string; category: Category; source_ref: string; source_name: string; ra: number; dec: number; difficulty: number; status: string;
  training: number; kind: "real" | "training"; instructions: string; data: Record<string, unknown>; candidate_id: string | null;
  observations: Obs[] | null; observation_count: number | null; n_submissions: number; n_reviews?: number;
  my_submissions?: { id: string; qc_status: string; submitted_at: string }[];
}
export interface Reward { board: "contribution" | "participation"; kind: string; points: number; factor: number; note?: string | null; challenge?: string }
export interface Achievement { key: string; name: string; description: string; icon: string; rarity: string; kind: string; reward_xp?: number; unlocked_at?: string | null }
export interface Level { level: number; title: string; xp: number; level_floor: number; next_level_at: number | null; progress: number | null }
export interface QcCheck { check: string; result: "pass" | "fail" | "skipped"; severity: string; detail: string }
export interface Qc {
  status: string; checks: QcCheck[]; errors: string[]; missing: string[]; warnings: string[]; evidence_score: number; high_quality: boolean;
  label: string | null; disclaimer: string; [k: string]: unknown;
}
export interface SubmitResult {
  contribution_id: string; qc: Qc; rewards: Reward[]; achievements: Achievement[]; totals: Totals; totals_before: Totals;
  rank: { all: number | null; week: number | null }; rank_before: { all: number | null; week: number | null }; level: Level; training?: boolean;
}
export interface Totals { contribution_xp: number; participation_xp: number; total_xp: number }
export interface Profile extends Totals {
  id: number; username: string; display_name: string | null; bio: string | null; role: "player" | "reviewer" | "admin"; created_at: string;
  level: Level; stats: Record<string, number>;
  streak: { current: number; longest: number; last_date: string | null; total_active_days: number; milestones: number[] };
  achievements: Achievement[]; specialisations: Category[]; derived_specialisations: { category: Category; count: number }[];
  ranks: { all: number | null; week: number | null; month: number | null };
  tz_name?: string | null; share_attribution?: boolean; pseudonym?: string;
}

const KEY = "spherex.game.token";
function loadToken(): string | null { try { return localStorage.getItem(KEY); } catch { return null; } }
function saveToken(t: string | null) { try { if (t) localStorage.setItem(KEY, t); else localStorage.removeItem(KEY); } catch { /* private mode */ } }

export class GameError extends Error { constructor(msg: string, readonly status: number) { super(msg); } }

let token = loadToken();
export async function gfetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.body ? { "Content-Type": "application/json" } : {}), ...(init.headers as Record<string, string> ?? {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(`/api/game${path}`, { ...init, headers });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new GameError(typeof body.detail === "string" ? body.detail : `Request failed (${r.status})`, r.status);
  }
  return r.json();
}
export const post = <T,>(path: string, body: unknown) => gfetch<T>(path, { method: "POST", body: JSON.stringify(body) });

interface Toast { id: number; title: string; body?: string; tone: "good" | "info" | "warn" }
interface Ctx {
  me: Profile | null; loading: boolean; refresh: () => Promise<void>;
  login: (u: string, p: string) => Promise<void>; register: (u: string, p: string, d?: string) => Promise<void>; logout: () => Promise<void>;
  toast: (t: Omit<Toast, "id">) => void; celebrate: (rewards: Reward[], achievements: Achievement[]) => void;
}
const GameCtx = createContext<Ctx | null>(null);
export const useGame = () => { const c = useContext(GameCtx); if (!c) throw new Error("GameProvider missing"); return c; };

export function GameProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const checked = useRef(false);

  const toast = useCallback((t: Omit<Toast, "id">) => {
    const id = ++seq.current;
    setToasts((x) => [...x.slice(-3), { ...t, id }]);
    setTimeout(() => setToasts((x) => x.filter((y) => y.id !== id)), 5200);
  }, []);
  const celebrate = useCallback((rewards: Reward[], achievements: Achievement[]) => {
    for (const r of rewards) {
      toast({ tone: r.board === "contribution" ? "good" : "info",
        title: `+${r.points} ${r.board === "contribution" ? "contribution points" : "XP"}`,
        body: r.challenge ? `Daily challenge: ${r.challenge}` : `${r.note ?? r.kind.replace(/_/g, " ")}${r.factor < 1 ? ` (×${r.factor}, repeated today)` : ""}` });
    }
    for (const a of achievements) toast({ tone: "good", title: `Achievement unlocked: ${a.name}`, body: a.description });
  }, [toast]);

  const refresh = useCallback(async () => {
    if (!token) { setMe(null); setLoading(false); return; }
    try { setMe(await gfetch<Profile>("/me")); }
    catch (e) { if ((e as GameError).status === 401) { token = null; saveToken(null); setMe(null); } }
    finally { setLoading(false); }
  }, []);

  const checkin = useCallback(async () => {
    if (!token || checked.current) return;
    checked.current = true;
    let tz: string | null = null;
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone ?? null; } catch { /* ignore */ }
    try {
      const r = await post<{ new_day: boolean; rewards: Reward[]; milestones: number[]; achievements: Achievement[]; streak: { current: number } }>(
        "/checkin", { tz_name: tz, offset_min: -new Date().getTimezoneOffset() });
      if (r.new_day) {
        toast({ tone: "info", title: `Day ${r.streak.current} streak`, body: "Logged in today. Login XP counts toward your level, not the contribution board." });
        for (const m of r.milestones) toast({ tone: "good", title: `${m}-day streak!` });
        celebrate(r.rewards.filter((x) => x.kind !== "daily_login"), r.achievements);
        await refresh();
      }
    } catch { /* not fatal */ }
  }, [toast, celebrate, refresh]);

  useEffect(() => { refresh().then(checkin); }, [refresh, checkin]);

  const value = useMemo<Ctx>(() => ({
    me, loading, refresh, toast, celebrate,
    login: async (u, p) => { token = (await post<{ token: string }>("/auth/login", { username: u, password: p })).token; saveToken(token); checked.current = false; await refresh(); await checkin(); },
    register: async (u, p, d) => { token = (await post<{ token: string }>("/auth/register", { username: u, password: p, display_name: d || null })).token; saveToken(token); checked.current = false; await refresh(); await checkin(); },
    logout: async () => { try { await post("/auth/logout", {}); } catch { /* ignore */ } token = null; saveToken(null); setMe(null); },
  }), [me, loading, refresh, toast, celebrate, checkin]);

  return (
    <GameCtx.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-[min(360px,calc(100%-32px))] flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="glass-strong pointer-events-auto rounded-xl px-4 py-3 text-sm">
            <div className={`font-semibold ${t.tone === "good" ? "text-emerald-200" : t.tone === "warn" ? "text-amber-200" : "text-sky-100"}`}>{t.title}</div>
            {t.body && <div className="mt-0.5 text-xs text-slate-400">{t.body}</div>}
          </div>
        ))}
      </div>
    </GameCtx.Provider>
  );
}

export const fmtPts = (n: number | null | undefined) => (n == null ? "–" : Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1));
export const statusTone: Record<string, string> = {
  passed: "text-emerald-300 border-emerald-300/30", passed_with_warnings: "text-amber-200 border-amber-300/30", incomplete: "text-slate-300 border-white/15",
  duplicate: "text-slate-400 border-white/10", rejected: "text-red-300 border-red-300/30", pending: "text-slate-300 border-white/15",
};
export const statusLabel = (s: string) => s.replace(/_/g, " ");
