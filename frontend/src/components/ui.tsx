import { useState, type ReactNode } from "react";
import { GLOSSARY } from "../lib/glossary";

export function Panel({ title, children, actions, defaultOpen = true, className }: { title: string; children: ReactNode; actions?: ReactNode; defaultOpen?: boolean; className?: string }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`border-b border-slate-800 ${className ?? ""}`}>
      <header className="flex items-center gap-2 px-3 py-2">
        <button type="button" className="flex flex-1 items-center gap-1 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400 hover:text-slate-200"
          aria-expanded={open} onClick={() => setOpen(!open)}>
          <span className={`inline-block transition-transform ${open ? "rotate-90" : ""}`}>›</span>
          {title}
        </button>
        {actions}
      </header>
      {open && <div className="space-y-2 px-3 pb-3 text-sm">{children}</div>}
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
    <select aria-label={label} className="max-w-40 rounded border border-slate-700 bg-slate-800 px-1.5 py-1 text-xs text-slate-100" value={value} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export function Slider({ value, min, max, step, onChange, label, format }: { value: number; min: number; max: number; step: number; onChange: (v: number) => void; label: string; format?: (v: number) => string }) {
  return (
    <Row label={label}>
      <input type="range" aria-label={label} min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-28 accent-amber-400" />
      <span className="w-10 text-right font-mono text-[11px] text-slate-400">{format ? format(value) : value}</span>
    </Row>
  );
}

export function Toggle({ checked, onChange, label, color, hint, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; color?: string; hint?: string; disabled?: boolean }) {
  return (
    <label className={`flex cursor-pointer items-center gap-2 text-xs ${disabled ? "opacity-50" : "text-slate-200"}`} title={hint}>
      <input type="checkbox" className="accent-amber-400" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      {color && <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />}
      <span>{label}</span>
    </label>
  );
}

export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string; title?: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="grid grid-cols-3 gap-1">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} title={o.title} aria-label={o.title ? `${o.label}: ${o.title}` : o.label}
          className={`rounded px-1.5 py-1.5 text-xs ${value === o.value ? "bg-amber-500 font-semibold text-slate-950" : "bg-slate-800 text-slate-200 hover:bg-slate-700"}`}
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
      <dd className={`min-w-0 truncate text-right text-slate-100 ${mono ? "font-mono" : ""}`} title={typeof v === "string" ? v : undefined}>{v ?? "—"}</dd>
    </div>
  );
}

export function Note({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" | "error" }) {
  const cls = tone === "error" ? "border-red-700 bg-red-950/50 text-red-200" : tone === "warn" ? "border-amber-700/60 bg-amber-950/40 text-amber-100" : "border-slate-700 bg-slate-800/60 text-slate-300";
  return <div className={`rounded border px-2 py-1.5 text-xs ${cls}`}>{children}</div>;
}

/** A technical term with a plain-language tooltip (hover, focus or tap). */
export function Term({ k, children }: { k: keyof typeof GLOSSARY | string; children: ReactNode }) {
  const tip = GLOSSARY[k];
  if (!tip) return <>{children}</>;
  return (
    <abbr tabIndex={0} title={tip}
      className="cursor-help underline decoration-slate-500 decoration-dotted underline-offset-2 focus:outline-none focus-visible:ring-1 focus-visible:ring-amber-400"
>
      {children}
    </abbr>
  );
}
