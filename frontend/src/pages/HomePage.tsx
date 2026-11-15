import { Suspense, lazy } from "react";
import { Link, useNavigate } from "react-router-dom";
import { SearchBar } from "../components/SearchBar";
import { ObservedSky } from "../components/ObservedSky";
import { BrushUnderline } from "../components/BrushUnderline";
import { NavBar } from "../components/NavBar";
import { useGlassSpecular } from "../lib/useGlassSpecular";
import { buildUrl } from "../lib/urlState";

// three.js is heavy: load the 3D scenes after the page so first paint stays fast.
const Starfield = lazy(() => import("../components/Starfield"));
const DraggableGalaxy = lazy(() => import("../components/DraggableGalaxy"));

export default function HomePage() {
  const navigate = useNavigate();
  useGlassSpecular();
  return (
    <div className="home relative min-h-screen overflow-x-clip text-neutral-200 antialiased">
      <div className="fixed inset-0 -z-10 bg-black" aria-hidden />
      <Suspense fallback={null}><Starfield /></Suspense>
      <Suspense fallback={null}><DraggableGalaxy /></Suspense>

      {/* Content sits above the galaxy; gaps between cards let pointer events through to the galaxy and the star field. */}
      <div className="pointer-events-none relative z-20 [&_.glass]:pointer-events-auto">
        <div className="pointer-events-auto"><NavBar /></div>

        <main className="w-full px-4 pb-20 sm:px-6 lg:px-10 2xl:px-16">
          {/* room for the galaxy above the hero on small screens */}
          <div className="h-[300px] sm:h-[360px] lg:hidden" />

          <div>
            <section data-galaxy-row className="pointer-events-auto max-w-2xl pt-2 lg:pt-28">
              <p className="font-mono text-xs text-neutral-400">NASA SPHEREx — all-sky infrared survey, 0.75–5 µm</p>
              <h1 className="mt-6 text-white">
                <span className="block text-4xl font-medium leading-none tracking-[-0.03em] sm:text-6xl 2xl:text-7xl">Watch the</span>
                <span className="mt-3 block font-hand text-5xl font-normal leading-[1.15] tracking-normal [word-spacing:-0.12em] sm:mt-5 sm:text-7xl 2xl:text-8xl"><span className="relative inline-block">Infrared<BrushUnderline className="-bottom-[0.24em] -left-[3%] h-[0.22em] w-[106%]" /></span> <span className="uppercase">sky</span></span>
                <span className="mt-3 block text-4xl font-medium leading-none tracking-[-0.03em] text-neutral-500 sm:mt-5 sm:text-6xl 2xl:text-7xl">move.</span>
              </h1>
              <p className="mt-6 max-w-xl text-base leading-relaxed text-neutral-400 2xl:text-lg">
                SPHEREx maps the whole sky in 102 infrared colours and returns to every spot about every six months.
                Step through every image of a position in time order: nearby stars and brown dwarfs creep across the field;
                variable stars brighten and fade.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <a href="#search" className="btn-primary px-5 py-2.5 text-sm">Start exploring</a>
                <Link to="/map" className="glass glass-hover glass-pill px-5 py-2.5 text-sm text-neutral-100">Open the 3D Atlas</Link>
              </div>
            </section>

            {/* Below the hero buttons: the observed-sky box on the left, the two square cards on the right.
                The galaxy starts above the cards (data-galaxy-anchor), level with the hero text. */}
            <div className="mt-12 flex flex-col gap-6 lg:mt-24 xl:flex-row xl:items-stretch xl:justify-between">
            <ObservedSky className="order-2 w-full xl:order-1 xl:min-w-0 xl:max-w-[560px] xl:flex-1 2xl:ml-[6%] 2xl:w-[560px] 2xl:flex-none" />
            <div data-galaxy-anchor className="order-1 grid w-full max-w-[652px] gap-6 sm:grid-cols-2 lg:ml-auto lg:mr-[8%] xl:order-2 xl:mr-0 xl:w-[652px] xl:shrink-0 2xl:mr-[16%]">
              <div id="search" className="glass glass-form flex min-w-0 scroll-mt-28 flex-col p-5 sm:min-h-[314px]">
                <span className="font-mono text-xs text-neutral-500">Search</span>
                <h2 className="mt-2 text-xl font-medium tracking-tight text-white">Search the sky</h2>
                <p className="mt-2 text-sm leading-relaxed text-neutral-400">A name or sky coordinates. Every SPHEREx image of that spot opens as a time-lapse.</p>
                <div className="mt-auto pt-5">
                  <SearchBar initial={null} onSubmit={(p) => navigate(buildUrl(p))} />
                </div>
              </div>

              <Link to="/map" className="glass glass-hover group flex min-w-0 flex-col p-5 sm:min-h-[314px]">
                <span className="font-mono text-xs text-neutral-500">Atlas — 3D</span>
                <h2 className="mt-2 text-xl font-medium tracking-tight text-white">SPHEREx Atlas</h2>
                <p className="mt-2 text-sm leading-relaxed text-neutral-400">Fly through real stars, brown dwarfs and planetary systems at their measured distances.</p>
                <dl className="mt-auto grid grid-cols-2 gap-4 pt-5">
                  <div><dt className="text-xs text-neutral-500">Objects</dt><dd className="mt-1 text-xl font-medium tracking-tight text-white">617k</dd></div>
                  <div><dt className="text-xs text-neutral-500">Planetary systems</dt><dd className="mt-1 text-xl font-medium tracking-tight text-white">4.8k</dd></div>
                </dl>
                <span className="mt-6 flex items-center justify-between border-t border-white/10 pt-4 text-sm text-neutral-200">
                  Open the map <span className="transition-transform group-hover:translate-x-0.5">→</span>
                </span>
              </Link>
            </div>
            </div>
          </div>

          {/* Feature card (template: title, description, Play, links on the left; interactive asset on the right) */}
          <section className="mt-24">
            <div className="glass grid items-center gap-8 p-6 sm:p-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] lg:gap-12 lg:p-10">
              <div className="flex flex-col">
                <h2 className="text-3xl font-medium tracking-tight text-white sm:text-4xl">Spot the Mover</h2>
                <p className="mt-4 max-w-md text-base leading-relaxed text-neutral-400">
                  Two SPHEREx images of the same patch of sky, taken months apart. Somewhere in the field a nearby star
                  or brown dwarf has shifted. Blink between them and find it before the clock runs out.
                </p>
                <div className="mt-8">
                  <Link to="/science" className="btn-primary px-6 py-2.5 text-sm">Play</Link>
                </div>
                <div className="mt-6 flex gap-5 text-sm">
                  <Link to="/science/leaderboard" className="text-neutral-400 underline decoration-neutral-600 underline-offset-4 hover:text-white hover:decoration-neutral-200">Leaderboards</Link>
                  <Link to="/about" className="text-neutral-400 underline decoration-neutral-600 underline-offset-4 hover:text-white hover:decoration-neutral-200">Help</Link>
                </div>
              </div>

              {/* Interactive asset (placeholder image for now) */}
              <div className="relative aspect-[4/3] overflow-hidden rounded-[22px] border border-white/15 bg-black/40">
                <img src="/game-placeholder.svg" alt="Placeholder for the interactive asset" className="h-full w-full object-cover" />
              </div>
            </div>
          </section>

          <footer className="pointer-events-auto mt-16 flex flex-col gap-6 border-t border-white/10 pt-6 text-sm text-neutral-500 sm:flex-row sm:justify-between">
            <p className="max-w-2xl">
              New to this? Read <Link to="/about" className="text-neutral-200 underline decoration-neutral-600 underline-offset-4 hover:decoration-neutral-200">About &amp; help</Link>: what SPHEREx is, why objects move, how to read
              difference images, and why a "candidate" is not a discovery.
            </p>
            <p className="max-w-md text-xs sm:text-right">
              Data: NASA SPHEREx via NASA/IPAC IRSA. Catalogues: ESA Gaia DR3, CatWISE2020, SIMBAD. Names resolved by CDS Sesame. Built for NASA Space Apps 2026.
            </p>
          </footer>
        </main>
      </div>
    </div>
  );
}
