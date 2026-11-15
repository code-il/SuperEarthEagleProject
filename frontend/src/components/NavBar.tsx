import { Link, NavLink } from "react-router-dom";
import { api } from "../lib/api";

const link = ({ isActive }: { isActive: boolean }) => `rounded px-2 py-1 text-sm ${isActive ? "text-amber-300" : "text-slate-300 hover:text-white"}`;

/** Site navigation for the Targets and About pages (the Atlas and its Explore pop-up have their own bars). */
export function NavBar() {
  return (
    <nav className="mx-auto flex max-w-5xl flex-wrap items-center gap-2 px-4 pt-4 sm:px-6" aria-label="Main">
      <Link to="/" className="mr-auto flex items-center gap-2" aria-label="SPHEREx Atlas">
        <img src="/favicon.svg" alt="" className="h-7 w-7" />
        <span className="text-sm font-bold tracking-wide">SPHEREx <span className="text-amber-400">Explorer</span></span>
        {api.usingMocks && <span className="rounded bg-fuchsia-700 px-2 py-0.5 text-[11px] font-semibold">MOCK DATA</span>}
      </Link>
      <NavLink to="/" end className={link}>3D Atlas</NavLink>
      <NavLink to="/targets" className={link}>Challenge targets</NavLink>
      <NavLink to="/explore" className={link}>Explore images</NavLink>
      <NavLink to="/about" className={link}>About &amp; help</NavLink>
    </nav>
  );
}
