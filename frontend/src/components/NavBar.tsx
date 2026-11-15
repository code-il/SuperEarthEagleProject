import { Link, NavLink } from "react-router-dom";
import { api } from "../lib/api";

const link = ({ isActive }: { isActive: boolean }) => `btn btn-ghost h-8 px-2.5 ${isActive ? "!text-white bg-white/[0.08]" : ""}`;

/** Site navigation for the Targets and About pages (the Atlas and its Explore pop-up have their own bars). */
export function NavBar() {
  return (
    <nav className="glass mx-auto mt-4 flex max-w-5xl flex-wrap items-center gap-1 rounded-2xl py-1.5 pl-3 pr-1.5 sm:mx-6 lg:mx-auto" aria-label="Main">
      <Link to="/" className="mr-auto flex items-center gap-2" aria-label="SPHEREx Atlas">
        <img src="/favicon.svg" alt="" className="h-5 w-5" />
        <span className="text-[13.5px] font-semibold tracking-tight">SPHEREx <span className="font-normal text-slate-400">Atlas</span></span>
        {api.usingMocks && <span className="rounded bg-fuchsia-700 px-2 py-0.5 text-[11px] font-semibold">MOCK DATA</span>}
      </Link>
      <NavLink to="/" end className={link}>3D Atlas</NavLink>
      <NavLink to="/targets" className={link}>Targets</NavLink>
      <NavLink to="/explore" className={link}>Images</NavLink>
      <NavLink to="/science" className={link}>Citizen science</NavLink>
      <NavLink to="/about" className={link}>About</NavLink>
    </nav>
  );
}
