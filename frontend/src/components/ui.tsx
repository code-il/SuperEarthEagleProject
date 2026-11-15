import { useState, type ReactNode } from "react";
import { GLOSSARY } from "../lib/glossary";

export function Panel({ title, children, actions, defaultOpen = true, className }: { title: string; children: ReactNode; actions?: ReactNode; defaultOpen?: boolean; className?: string }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`border-b border-white/[0.06] ${className ?? ""}`}>
      <header className="flex items-center gap-2 px-4 py-2.5">
        <button type="button" className="eyebrow flex flex-1 items-center gap-1.5 text-left hover:!text-slate-200"
          aria-expanded={open} onClick={() => setOpen(!open)}>
          <span className={`inline-block transition-transform ${open ? "rotate-90" : ""}`}>›</span>
          {title}
        </button>
        {actions}
      </header>
      {open && <div className="space-y-2.5 px-4 pb-4 text-sm">{children}</div>}
    </section>
  );
}

export function Row({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex items-center justify-between gap-2 text-xs text-slate-300" title={hint}>
      <span className="shrink-0">{label}</span>
      <span className="flex min-w-0 items-center justify-end gap-1">{children}</span>
    </label>
  );
}

export function Select<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label?: string }) {
  return (
    <select aria-label={label} className="max-w-40 rounded-lg border border-white/10 bg-white/[0.05] px-2 py-1 text-xs text-slate-100" value={value} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export function Slider({ value, min, max, step, onChange, label, format }: { value: number; min: number; max: number; step: number; onChange: (v: number) => void; label: string; format?: (v: number) => string }) {
  return (
    <Row label={label}>
      <input type="range" aria-label={label} min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-28" />
      <span className="num w-10 text-right text-[11px] text-slate-400">{format ? format(value) : value}</span>
    </Row>
  );
}

export function Toggle({ checked, onChange, label, color, hint, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; color?: string; hint?: string; disabled?: boolean }) {
  return (
    <label className={`flex cursor-pointer items-center gap-2 text-xs ${disabled ? "opacity-50" : "text-slate-200"}`} title={hint}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      {color && <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />}
      <span>{label}</span>
    </label>
  );
}

export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string; title?: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="seg grid grid-cols-3">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} title={o.title} aria-label={o.title ? `${o.label}: ${o.title}` : o.label}
          onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function KV({ k, v, mono = true }: { k: ReactNode; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-3 text-xs">
      <dt className="shrink-0 text-slate-400">{k}</dt>
      <dd className={`min-w-0 truncate text-right text-slate-100 ${mono ? "num" : ""}`} title={typeof v === "string" ? v : undefined}>{v ?? "—"}</dd>
    </div>
  );
}

export function Note({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" | "error" }) {
  const cls = tone === "error" ? "border-red-400/30 bg-red-500/10 text-red-100" : tone === "warn" ? "border-amber-300/25 bg-amber-400/[0.08] text-amber-100" : "border-white/[0.08] bg-white/[0.03] text-slate-300";
  return <div className={`rounded-xl border px-3 py-2 text-xs leading-relaxed ${cls}`}>{children}</div>;
}

/** A technical term with a plain-language tooltip (hover, focus or tap). */
export function Term({ k, children }: { k: keyof typeof GLOSSARY | string; children: ReactNode }) {
  const tip = GLOSSARY[k];
  if (!tip) return <>{children}</>;
  return (
    <abbr tabIndex={0} title={tip}
      className="cursor-help underline decoration-slate-500 decoration-dotted underline-offset-2 focus:outline-none focus-visible:ring-1 focus-visible:ring-sky-300"
>
      {children}
    </abbr>
  );
}
