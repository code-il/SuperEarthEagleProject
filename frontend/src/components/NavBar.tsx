import { Link, NavLink } from "react-router-dom";
import { api } from "../lib/api";

const link = ({ isActive }: { isActive: boolean }) =>
  `rounded-full px-2.5 py-1.5 text-sm sm:px-3 transition-colors ${isActive
    ? "bg-white/10 text-white"
    : "text-neutral-400 hover:text-white"}`;

/** Floating liquid-glass navigation for the Home and About pages (the Explore page has its own compact bar). */
export function NavBar() {
  return (
    <header className="home sticky top-3 z-40 px-4 pt-3 sm:px-6 lg:px-10 2xl:px-16">
      <nav className="glass flex w-full flex-wrap items-center gap-1 rounded-[28px] px-3 py-2" aria-label="Main">
        <Link to="/" className="mr-auto flex items-center gap-2 rounded-full py-0.5 pl-1 pr-3" aria-label="SPHEREx Explorer home">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="text-sm font-medium tracking-tight">SPHEREx <span className="text-neutral-400">Explorer</span></span>
          {api.usingMocks && <span className="rounded-full bg-fuchsia-700 px-2 py-0.5 text-[11px] font-semibold">MOCK DATA</span>}
        </Link>
        <NavLink to="/" end className={link}>Home</NavLink>
        <NavLink to="/map" className={link}>3D Atlas</NavLink>
        <NavLink to="/explore" className={link}>Explore</NavLink>
        <NavLink to="/science" className={link}>Citizen science</NavLink>
        <NavLink to="/about" className={link}>About &amp; help</NavLink>
      </nav>
    </header>
  );
}
