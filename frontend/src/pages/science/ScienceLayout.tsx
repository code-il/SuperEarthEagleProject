/** Shell of the citizen-science section: glass sidebar, player summary, provenance legend and disclaimer. */
import { Suspense, lazy, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { NavBar } from "../../components/NavBar";
import { GameProvider, fmtPts, useGame } from "../../lib/game";
import { useGlassSpecular } from "../../lib/useGlassSpecular";

// Same star-field backdrop as the home page, loaded after first paint.
const Starfield = lazy(() => import("../../components/Starfield"));

const NAV: [string, string, boolean?][] = [
  ["/science", "Explore"], ["/science/daily", "Daily Survey"], ["/science/investigate", "Investigate"], ["/science/review", "Review"], ["/science/leaderboard", "Leaderboard"],
  ["/science/profile", "My profile"], ["/science/progress", "Global progress"], ["/science/contributions", "Scientific contributions"],
];

export default function ScienceLayout() {
  return <GameProvider><Shell /></GameProvider>;
}

function Shell() {
  const { me, logout } = useGame();
  const loc = useLocation();
  useGlassSpecular();
  return (
    <div className="home relative min-h-screen overflow-x-clip text-neutral-200 antialiased">
      <div className="fixed inset-0 -z-10 bg-black" aria-hidden />
      <Suspense fallback={null}><Starfield /></Suspense>
      <NavBar />

      <div className="relative z-10 grid w-full grid-cols-1 gap-6 px-4 pb-6 pt-8 sm:px-6 lg:grid-cols-[230px_1fr] lg:px-10 2xl:px-16">
        <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
          <div className="glass flex items-center gap-2 rounded-[20px] p-3">
            <div className="min-w-0 flex-1">
              <div className="font-mono text-[11px] text-neutral-500">Citizen science</div>
              <div className="truncate text-sm font-medium text-white">{me ? (me.display_name || me.username) : "Not signed in"}</div>
            </div>
            {me ? <button onClick={logout} className="glass glass-hover glass-pill px-3 py-1.5 text-xs text-neutral-200">Sign out</button>
              : <Link to={`/science/login?next=${encodeURIComponent(loc.pathname)}`} className="btn-primary px-3.5 py-1.5 text-xs">Sign in</Link>}
          </div>
          <nav className="glass flex gap-1 overflow-x-auto rounded-[20px] p-1.5 lg:flex-col" aria-label="Citizen science">
            {[...NAV, ...(me?.role === "admin" ? [["/science/admin", "Administration"] as [string, string]] : [])].map(([to, label]) => (
              <NavLink key={to} to={to} end={to === "/science"}
                className={({ isActive }) => `whitespace-nowrap rounded-xl px-3 py-2 text-[13px] transition ${isActive ? "bg-white/[0.09] text-white" : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"}`}>
                {label}
              </NavLink>
            ))}
          </nav>
          {me && <PlayerMini />}
          <Legend />
        </aside>
        <main className="min-w-0"><Outlet /></main>
      </div>
      <Disclaimer />
    </div>
  );
}

function PlayerMini() {
  const { me } = useGame();
  if (!me) return null;
  const lv = me.level;
  return (
    <div className="glass hidden rounded-2xl p-4 text-xs lg:block">
      <div className="flex items-baseline justify-between"><span className="text-slate-400">Level {lv.level}</span><span className="text-slate-200">{lv.title}</span></div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${(lv.progress ?? 1) * 100}%` }} /></div>
      <div className="num mt-1 text-[11px] text-slate-500">{fmtPts(me.total_xp)} XP{lv.next_level_at != null && ` / ${lv.next_level_at.toLocaleString()}`}</div>
      <dl className="mt-3 grid grid-cols-2 gap-2">
        <Stat k="Contribution" v={fmtPts(me.contribution_xp)} />
        <Stat k="Streak" v={`${me.streak.current} d`} />
        <Stat k="Rank (all)" v={me.ranks.all ? `#${me.ranks.all}` : "–"} />
        <Stat k="This week" v={me.ranks.week ? `#${me.ranks.week}` : "–"} />
      </dl>
    </div>
  );
}

function Legend() {
  return (
    <div className="glass hidden rounded-2xl p-4 text-[11.5px] leading-relaxed text-slate-400 lg:block">
      <div className="eyebrow mb-2">What you are looking at</div>
      <ul className="space-y-1.5">
        <li><Kind k="real" /> Real SPHEREx observations and catalogue data (Gaia DR3, SIMBAD, NASA Exoplanet Archive).</li>
        <li><Kind k="training" /> Training task: the answer is already known; earns XP, not contribution points.</li>
        <li><Kind k="simulated" /> Simulated demonstration records, never real reviews.</li>
        <li><Kind k="illustration" /> Illustration or prediction, not an observation.</li>
      </ul>
    </div>
  );
}

export function Kind({ k }: { k: "real" | "training" | "simulated" | "illustration" | "community" }) {
  const m = {
    real: ["Real data", "border-emerald-300/30 text-emerald-200"], training: ["Training", "border-sky-300/30 text-sky-200"],
    simulated: ["Simulated", "border-fuchsia-300/40 text-fuchsia-200"], illustration: ["Illustration", "border-amber-300/30 text-amber-200"],
    community: ["Community", "border-white/15 text-slate-300"],
  }[k];
  return <span className={`chip ${m[1]} py-0 text-[10.5px]`}>{m[0]}</span>;
}

function Disclaimer() {
  return (
    <footer className="relative z-10 w-full px-4 pb-10 text-[11.5px] leading-relaxed text-neutral-500 sm:px-6 lg:px-10 2xl:px-16">
      <div className="border-t border-white/[0.06] pt-4">
        An independent citizen-science project built for the NASA International Space Apps Challenge. It is not produced, endorsed or
        operated by NASA, Caltech/IPAC or the SPHEREx team. Automated quality checks and community agreement are not scientific
        validation; only results recorded from an external scientific review appear as validated. SPHEREx data courtesy of NASA/JPL-Caltech
        via IRSA. See <a className="text-slate-300 underline decoration-white/20 hover:decoration-white/60" href="https://github.com/code-il/cosmIO/blob/main/docs/SOURCES.md" target="_blank" rel="noreferrer">sources, licences and AI disclosure</a>.
      </div>
    </footer>
  );
}

// ------------------------------------------------------------------ small shared pieces
export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`glass rounded-2xl ${className}`}>
      {title && <header className="flex items-center justify-between gap-3 border-b border-white/[0.06] px-5 py-3"><h2 className="text-sm font-semibold text-slate-100">{title}</h2>{actions}</header>}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function Stat({ k, v, hint }: { k: string; v: ReactNode; hint?: string }) {
  return (
    <div title={hint}>
      <dt className="text-[11px] text-slate-500">{k}</dt>
      <dd className="num text-[15px] text-slate-100">{v}</dd>
    </div>
  );
}

export function PageTitle({ title, sub, right }: { title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight text-white">{title}</h1>
        {sub && <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-slate-400">{sub}</p>}
      </div>
      {right}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center text-sm text-slate-400">{children}</div>;
}

export function NeedSignIn({ what }: { what: string }) {
  const loc = useLocation();
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-300">
        <span>Sign in to {what}.</span>
        <Link to={`/science/login?next=${encodeURIComponent(loc.pathname)}`} className="btn btn-primary">Sign in or create an account</Link>
      </div>
    </Card>
  );
}
