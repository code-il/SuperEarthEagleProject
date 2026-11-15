import type { OverlaySettings } from "../types";
import { Panel, Slider, Toggle } from "./ui";

export interface OverlayStatus {
  gaia?: { count: number; loading: boolean; error?: string | null };
  catwise?: { count: number; loading: boolean; error?: string | null };
  sources?: { count: number; loading: boolean; error?: string | null };
  hasTarget: boolean;
}

function status(s?: { count: number; loading: boolean; error?: string | null }) {
  if (!s) return null;
  if (s.loading) return <span className="text-slate-500">loading…</span>;
  if (s.error) return <span className="text-red-400" title={s.error}>unavailable</span>;
  return <span className="text-slate-500">{s.count}</span>;
}

export function OverlayControls({ value, onChange, st }: { value: OverlaySettings; onChange: (o: OverlaySettings) => void; st: OverlayStatus }) {
  const set = <K extends keyof OverlaySettings>(k: K, v: OverlaySettings[K]) => onChange({ ...value, [k]: v });
  return (
    <Panel title="Overlays">
      <Toggle color="#fbbf24" checked={value.target} onChange={(v) => set("target", v)} disabled={!st.hasTarget}
        label={<>Target (predicted position){!st.hasTarget && <span className="text-slate-500"> — coordinates query</span>}</>}
        hint="Where the named target should be on each frame's date, from its catalogue proper motion" />
      <Toggle color="#38bdf8" checked={value.gaia} onChange={(v) => set("gaia", v)}
        label={<span className="flex gap-2">Gaia DR3 {status(st.gaia)}</span>} hint="Gaia DR3 stars moved to each frame's date with their proper motions" />
      <Toggle color="#e879f9" checked={value.catwise} onChange={(v) => set("catwise", v)}
        label={<span className="flex gap-2">CatWISE2020 {status(st.catwise)}</span>} hint="Mid-infrared (WISE) sources with proper motions; good for brown dwarfs" />
      <Toggle color="#4ade80" checked={value.sources} onChange={(v) => set("sources", v)}
        label={<span className="flex gap-2">Detected sources (analysis) {status(st.sources)}</span>} hint="Markers from the analysis workstream (Person 4), when available" />
      <Toggle checked={value.labels} onChange={(v) => set("labels", v)} label="Names" />
      <Toggle checked={value.compass} onChange={(v) => set("compass", v)} label="Compass and scale bar" />
      <Slider label="Catalogue mag limit" min={8} max={21} step={0.5} value={value.maxMag} onChange={(v) => set("maxMag", v)} format={(v) => v.toFixed(1)} />
    </Panel>
  );
}
