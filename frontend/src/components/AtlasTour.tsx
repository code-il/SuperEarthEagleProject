/**
 * Guided tour of the Atlas: a card of steps, each of which can move the camera / open a panel and
 * highlights the part of the screen it talks about (``data-tour="…"`` attributes).
 */
import { useEffect, useState } from "react";

export interface TourStep {
  title: string;
  body: React.ReactNode;
  /** ``data-tour`` value of the element to highlight. */
  target?: string;
  /** Run when the step opens (fly the camera, open a panel, select an object…). */
  run?: () => void;
}

export function AtlasTour({ steps, step, onStep, onClose }: { steps: TourStep[]; step: number; onStep: (k: number) => void; onClose: () => void }) {
  const s = steps[step];
  const [rect, setRect] = useState<DOMRect | null>(null);

  useEffect(() => { s?.run?.(); }, [step]); // eslint-disable-line react-hooks/exhaustive-deps

  // Track the highlighted element (it may appear a moment later, e.g. the object card after a fly-to).
  useEffect(() => {
    if (!s?.target) { setRect(null); return; }
    const find = () => {
      const el = document.querySelector(`[data-tour="${s.target}"]`);
      const r = el?.getBoundingClientRect();
      setRect(r && r.width > 0 ? r : null);
    };
    find();
    const id = setInterval(find, 250);
    return () => clearInterval(id);
  }, [s?.target, step]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight" || e.key === "Enter") step < steps.length - 1 ? onStep(step + 1) : onClose();
      else if (e.key === "ArrowLeft" && step > 0) onStep(step - 1);
      else return;
      e.stopPropagation();
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [step, steps.length, onStep, onClose]);

  if (!s) return null;
  const last = step === steps.length - 1;
  return (
    <>
      {rect && (
        <div className="pointer-events-none fixed z-[45] rounded-2xl border-2 border-[var(--accent)] shadow-[0_0_0_9999px_rgba(1,3,10,0.5),0_0_24px_rgba(143,216,248,0.45)] transition-all duration-300"
          style={{ left: rect.left - 6, top: rect.top - 6, width: rect.width + 12, height: rect.height + 12 }} />
      )}
      <div role="dialog" aria-label={`Tour step ${step + 1} of ${steps.length}: ${s.title}`}
        className="glass-strong fixed bottom-16 left-1/2 z-50 w-[min(440px,calc(100%-24px))] -translate-x-1/2 rounded-2xl p-5 text-sm text-slate-200">
        <div className="eyebrow mb-1 flex items-center justify-between">
          <span>Tour · {step + 1} of {steps.length}</span>
          <button onClick={onClose} className="tracking-normal text-slate-400 hover:text-white" aria-label="End the tour">✕</button>
        </div>
        <h3 className="text-base font-semibold text-white">{s.title}</h3>
        <div className="mt-1.5 space-y-1.5 text-[13px] leading-relaxed text-slate-300">{s.body}</div>
        <div className="mt-3 flex items-center gap-2">
          <div className="flex flex-1 gap-1" aria-hidden>
            {steps.map((_, k) => <button key={k} tabIndex={-1} onClick={() => onStep(k)} className={`h-1 flex-1 rounded-full ${k <= step ? "bg-[var(--accent)]" : "bg-white/10"}`} />)}
          </div>
          <button disabled={step === 0} onClick={() => onStep(step - 1)} className="btn px-3 py-1 text-xs">Back</button>
          <button onClick={() => (last ? onClose() : onStep(step + 1))} className="btn btn-primary px-3 py-1 text-xs">{last ? "Start exploring" : "Next"}</button>
        </div>
      </div>
    </>
  );
}
