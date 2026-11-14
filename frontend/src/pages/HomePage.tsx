import { Link, useNavigate } from "react-router-dom";
import { SearchBar } from "../components/SearchBar";
import { NavBar } from "../components/NavBar";
import { buildUrl } from "../lib/urlState";
import { MANDATORY_TARGETS, WISE0855_COORDS } from "../lib/targets";

const STEPS: [string, string][] = [
  ["Search", "Type a star's name or sky coordinates. Names are looked up with their catalogue motion, so fast movers are found where they are now."],
  ["Retrieve", "Every SPHEREx image covering that spot is found in NASA's archive (IRSA), cut out and put on one common, north-up sky grid."],
  ["Play & compare", "Step through the images in time order, blink two dates, or subtract them to see what moved or changed."],
  ["Analyse", "Sources are detected in every visit, their motion and brightness measured and checked against star catalogues."],
];

export default function HomePage() {
  const navigate = useNavigate();
  return (
    <div className="min-h-screen bg-[radial-gradient(ellipse_at_top,#1e1b4b_0%,#020617_60%)] text-slate-100">
      <NavBar />
      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6 sm:py-14">
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Watch the infrared sky <span className="text-amber-400">move</span>.</h1>
        <p className="mt-3 max-w-2xl text-slate-300">
          NASA's SPHEREx telescope maps the whole sky in 102 infrared colours, 0.75 to 5 µm, and comes back to every spot about every six months.
          Pick a position and step through every SPHEREx image of it in time order. Nearby stars and brown dwarfs creep across the field;
          variable stars brighten and fade.
        </p>

        <div className="mt-8 rounded-lg border border-slate-800 bg-slate-900/70 p-4 pb-6">
          <SearchBar initial={null} onSubmit={(p) => navigate(buildUrl(p))} />
        </div>

        <h2 className="mt-10 text-sm font-semibold uppercase tracking-wider text-slate-400">Challenge targets</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {MANDATORY_TARGETS.map((t) => (
            <div key={t.key} className="group relative rounded-lg border border-slate-800 bg-slate-900/70 p-4 hover:border-amber-500/70">
              <Link to={buildUrl({ q: t.query, fov: t.key === "r-draconis" ? 0.08 : 0.05, detectors: [6] })} className="after:absolute after:inset-0">
                <div className="font-semibold group-hover:text-amber-300">{t.label}</div>
              </Link>
              <p className="mt-1 text-sm text-slate-400">{t.why}</p>
              <p className="mt-2 text-xs text-sky-300">Open time-lapse →</p>
              {t.key === "wise-0855" && (
                <Link to={buildUrl({ ra: WISE0855_COORDS.ra, dec: WISE0855_COORDS.dec, fov: 0.1, detectors: [6] })}
                  className="relative z-10 mt-1 block font-mono text-[11px] text-slate-400 hover:text-amber-300"
                  title="The challenge's supplied coordinates (AllWISE epoch). WISE 0855 moves 8″/yr, so by 2025–26 it is ~2′ from them: this opens a 0.1° field that still contains it.">
                  or at RA {WISE0855_COORDS.raText} Dec {WISE0855_COORDS.decText} (0.1° field: it has moved ~2′ since)
                </Link>
              )}
            </div>
          ))}
          <Link to="/explore?ra=269.452&dec=4.693&fov=0.1" className="rounded-lg border border-dashed border-slate-700 p-4 hover:border-amber-500/70">
            <div className="font-semibold">By coordinates</div>
            <p className="mt-1 font-mono text-xs text-slate-400">/explore?ra=269.452&amp;dec=4.693&amp;fov=0.1</p>
            <p className="mt-2 text-xs text-slate-400">Any URL like this opens the viewer directly, so you can share or bookmark a field.</p>
          </Link>
        </div>

        <h2 className="mt-12 text-sm font-semibold uppercase tracking-wider text-slate-400">How it works</h2>
        <ol className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map(([t, d], i) => (
            <li key={t} className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
              <div className="text-xs font-semibold text-amber-400">{i + 1}</div>
              <div className="font-semibold text-slate-100">{t}</div>
              <p className="mt-1 text-sm text-slate-400">{d}</p>
            </li>
          ))}
        </ol>

        <h2 className="mt-12 text-sm font-semibold uppercase tracking-wider text-slate-400">Exploring the sky</h2>
        <div className="mt-3 grid gap-6 text-sm text-slate-400 sm:grid-cols-3">
          <div><h3 className="font-semibold text-slate-200">Time-lapse</h3><p className="mt-1">Press play (or Space). The timeline shows the real gaps between visits. Each frame is at a different infrared colour, so filter by channel to compare like with like.</p></div>
          <div><h3 className="font-semibold text-slate-200">Compare</h3><p className="mt-1">Blink, side-by-side, PSF-matched difference and ratio images, and per-visit stacks. A mover shows up as a light/dark pair in a difference image.</p></div>
          <div><h3 className="font-semibold text-slate-200">Check</h3><p className="mt-1">Overlay Gaia and CatWISE stars moved to each frame's date, inspect detected sources, their motion, light curves and spectra, and read quality flags.</p></div>
        </div>
        <p className="mt-6 text-sm text-slate-400">
          New to this? Read <Link to="/about" className="text-sky-300 hover:underline">About &amp; help</Link>: what SPHEREx is, why objects move, how to read
          difference images, and why a "candidate" is not a discovery.
        </p>
        <p className="mt-10 text-xs text-slate-500">
          Data: NASA SPHEREx via NASA/IPAC IRSA. Catalogues: ESA Gaia DR3, CatWISE2020, SIMBAD. Names resolved by CDS Sesame. Built for NASA Space Apps 2026.
        </p>
      </div>
    </div>
  );
}
