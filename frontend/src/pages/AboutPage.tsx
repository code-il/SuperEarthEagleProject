/** /about: what SPHEREx is, how to read the viewer, and why a candidate is not a discovery. */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { NavBar } from "../components/NavBar";
import { GLOSSARY } from "../lib/glossary";

const TERMS: [string, string][] = [
  ["fov", "Field of view"], ["arcsec", "Arcsecond (″)"], ["proper_motion", "Proper motion"], ["wavelength", "Wavelength"],
  ["channel", "Spectral channel"], ["detector", "Detector (D1–D6)"], ["mjysr", "MJy/sr"], ["release", "Data release"],
  ["psf", "PSF"], ["registration", "Registration"], ["blink", "Blink"], ["difference", "Difference image"], ["stack", "Stack"],
  ["light_curve", "Light curve"], ["spectrum", "Spectrum"], ["saturated", "Saturated"], ["artefact", "Artefact"],
  ["sigma", "σ (significance)"], ["confidence", "Confidence"], ["candidate", "Candidate"], ["known", "Known"], ["zodi", "Zodiacal light"],
];

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-6 border-t border-white/[0.06] pt-6">
      <h2 className="text-lg font-semibold text-slate-100">{title}</h2>
      <div className="mt-2 space-y-3 text-sm leading-relaxed text-slate-300">{children}</div>
    </section>
  );
}

export default function AboutPage() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <NavBar />
      <main className="mx-auto max-w-3xl space-y-8 px-4 py-10 sm:px-6">
        <header>
          <h1 className="text-2xl font-bold">About &amp; help</h1>
          <nav className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-sky-300" aria-label="On this page">
            {[["spherex", "What is SPHEREx?"], ["motion", "Why objects move"], ["timelapse", "Reading a time-lapse"], ["difference", "Difference images"],
              ["analysis", "The analysis"], ["candidates", "Candidates vs discoveries"], ["report", "Reporting a candidate"], ["glossary", "Glossary"], ["data", "Data & credits"]]
              .map(([id, t]) => <a key={id} href={`#${id}`} className="hover:underline">{t}</a>)}
          </nav>
        </header>

        <Section id="spherex" title="What is SPHEREx?">
          <p>
            SPHEREx (Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer) is a NASA space telescope
            launched in March 2025. It maps the whole sky in 102 infrared colours from 0.75 to 5 µm, longer than the eye can see.
          </p>
          <p>
            It does not take colour pictures. Each image passes through a filter whose colour changes across the detector, and the telescope then
            steps across the sky. So any one position is seen many times, a little redder or bluer each time. A field gets a burst of such frames,
            spread over hours to weeks, roughly every six months. This viewer collects all frames of a position and lines them up in time.
          </p>
        </Section>

        <Section id="motion" title="Why do astronomical objects move?">
          <p>
            Stars orbit the Galaxy, and nearby ones shift visibly against the far background: this is <em>proper motion</em>. The closest stars and
            brown dwarfs move several arcseconds per year. Barnard's Star moves 10.4″/yr, so across a year of SPHEREx visits it crosses almost two pixels.
          </p>
          <p>
            Nearby objects also trace a small yearly loop (<em>parallax</em>) because we watch from a moving Earth. Asteroids and comets in our own
            Solar System are much faster: they move noticeably within minutes to hours, so they streak or jump between frames of one visit.
          </p>
        </Section>

        <Section id="timelapse" title="How to read a time-lapse">
          <ul className="list-disc space-y-1 pl-5">
            <li>The timeline shows the real gaps: dense bursts (one visit) separated by months. "Visits" mode squeezes the gaps so each visit is readable.</li>
            <li>Consecutive frames are at different wavelengths. A star that brightens and fades between neighbouring frames is usually just being seen in a different colour. Use <strong>Spectral channels</strong> to keep one colour.</li>
            <li>A real mover keeps drifting in one direction from visit to visit, while the background stars stay put. The dashed circle shows where the catalogue says the target should be on each date.</li>
            <li>Bright stars can saturate (the pixels fill up), leaving masked cores, halos and ghosts that change with the telescope's orientation. Check the <strong>Flags at target</strong> list.</li>
          </ul>
        </Section>

        <Section id="difference" title="What difference images show">
          <p>
            A difference image is one date minus another, after both are put on the same sky grid and matched in sharpness and background. Everything that did
            not change cancels to grey. A moving star leaves a <strong>light/dark pair</strong>: light where it is now, dark where it was. A brightening shows
            as a single light spot.
          </p>
          <p>
            Imperfect matching also leaves patterns around every bright star, so compare with neighbouring stars. The significance view (σ) tells you whether
            a residual stands out from the noise. Blink and side-by-side modes show the same comparison without subtracting.
          </p>
        </Section>

        <Section id="analysis" title="What the analysis does">
          <p>
            The analysis finds sources in every visit, links them across visits, fits a motion and a brightness model, compares each with Gaia DR3, CatWISE2020 and
            SIMBAD, and assigns a class: stationary, high proper motion, variable, transient or possible solar-system object. Each class comes with the measured
            criteria that put it there and a <em>confidence</em> from 0 to 1.
          </p>
          <p>
            Click a source in the Analysis panel to see its motion and catalogue match, and to load its light curve and spectrum into the bottom panel.
          </p>
        </Section>

        <Section id="candidates" title="Why a candidate is not a discovery">
          <p>A "candidate" means the measurements pass a set of tests and no catalogue entry explains it. It does <strong>not</strong> mean something new. Common explanations are:</p>
          <ul className="list-disc space-y-1 pl-5">
            <li><strong>Instrument artefacts:</strong> halos and ghosts of bright stars, cosmic-ray hits and edge effects.</li>
            <li><strong>Alignment errors:</strong> small misalignments between dates that look like motion.</li>
            <li><strong>Colour changes:</strong> brightness changes that are really frames taken at different wavelengths.</li>
            <li><strong>Blends:</strong> two stars closer than the 6″ pixels.</li>
            <li><strong>Catalogue gaps:</strong> catalogues that did not answer, or that list the object in pieces.</li>
          </ul>
          <p>
            A confidence of 0.9 means the criteria are strongly met, not a 90% chance of being real. Confirming anything needs independent data, such as other
            telescopes, other epochs and spectroscopy, and checks by experts.
          </p>
        </Section>

        <Section id="report" title="How to report an interesting candidate">
          <ol className="list-decimal space-y-1 pl-5">
            <li>Check it yourself first. Does it appear in more than one visit? Does it look the same in other detectors or channels? Is it far from bright stars? Does the "Why this class?" list pass comfortably?</li>
            <li>Look it up: the sky-context panel links to the field in Aladin Lite, and SIMBAD and VizieR at CDS hold other catalogues.</li>
            <li>Click <strong>Copy report</strong> in the source's detail panel. It copies the shareable link, position, motion with uncertainties and catalogue result.</li>
            <li>
              Share it with people who can follow it up. Moving brown-dwarf or star candidates go to the citizen-science project{" "}
              <a className="text-sky-300 hover:underline" href="https://www.zooniverse.org/projects/marckuchner/backyard-worlds-planet-9" target="_blank" rel="noreferrer">Backyard Worlds: Planet 9</a>{" "}
              (its Talk forum). Fast-moving Solar-System objects go to the{" "}
              <a className="text-sky-300 hover:underline" href="https://www.minorplanetcenter.net/" target="_blank" rel="noreferrer">Minor Planet Center</a>,
              after checking that the object is not already known.
            </li>
            <li>Always describe it as a candidate from SPHEREx data, include the link, and say what you checked.</li>
          </ol>
          <p className="text-xs text-slate-500">This site does not send anything anywhere; reporting is up to you.</p>
        </Section>

        <Section id="glossary" title="Glossary">
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            {TERMS.map(([k, t]) => (
              <div key={k}><dt className="font-semibold text-slate-200">{t}</dt><dd className="text-slate-400">{GLOSSARY[k]}</dd></div>
            ))}
          </dl>
          <p className="text-xs text-slate-500">Dotted-underlined words in the viewer show these explanations when you hover or tap them.</p>
        </Section>

        <Section id="data" title="Data & credits">
          <p>
            Images: NASA SPHEREx, from the NASA/IPAC Infrared Science Archive (IRSA), releases DR1 and QR3. Catalogues: ESA Gaia DR3, CatWISE2020, SIMBAD (CDS).
            Asteroids: IMCCE SkyBoT. Names: CDS Sesame. Sky context: Aladin Lite (CDS). Built for NASA Space Apps 2026, "Planet X and SPHEREx".
          </p>
          <p>
            <Link to="/explore?q=WISE%20J085510.83-071442.5&fov=0.05&det=6" className="text-amber-300 hover:underline">Try it: WISE 0855 →</Link>
          </p>
        </Section>
      </main>
    </div>
  );
}
