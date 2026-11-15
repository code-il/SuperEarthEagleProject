import type { Colormap, DisplaySettings, LimitsMode, SciencePlane, Stretch } from "../types";
import { DEFAULT_DISPLAY, colormapRgb } from "../lib/render";
import { Panel, Row, Select, Slider, Toggle } from "./ui";

const CMAPS: Colormap[] = ["gray", "inferno", "magma", "viridis"];

function Ramp({ cmap, invert }: { cmap: Colormap; invert: boolean }) {
  const stops = Array.from({ length: 11 }, (_, i) => {
    const t = invert ? 1 - i / 10 : i / 10;
    const [r, g, b] = colormapRgb(cmap, t);
    return `rgb(${r},${g},${b}) ${i * 10}%`;
  });
  return <div className="h-2 w-full rounded" style={{ background: `linear-gradient(90deg, ${stops.join(",")})` }} aria-hidden />;
}

/** Display-only controls. None of these change the science values. */
export function DisplayControls({ value, onChange, limits, units, diverging, showPlane = false }: { value: DisplaySettings; onChange: (d: DisplaySettings) => void; limits: [number, number]; units?: string; diverging?: boolean; showPlane?: boolean }) {
  const set = <K extends keyof DisplaySettings>(k: K, v: DisplaySettings[K]) => onChange({ ...value, [k]: v });
  return (
    <Panel title="Display" actions={
      <button type="button" className="text-[11px] text-slate-400 hover:text-amber-300" onClick={() => onChange(DEFAULT_DISPLAY)}>Reset</button>
    }>
      <Row label="Stretch">
        <Select<Stretch> label="Stretch" value={value.stretch} onChange={(v) => set("stretch", v)}
          options={[{ value: "linear", label: "linear" }, { value: "sqrt", label: "sqrt" }, { value: "log", label: "log" }, { value: "asinh", label: "asinh" }]} />
      </Row>
      <Row label="Colormap">
        <Select<Colormap> label="Colormap" value={value.colormap} onChange={(v) => set("colormap", v)} options={CMAPS.map((c) => ({ value: c, label: c }))} />
      </Row>
      <Ramp cmap={diverging && value.colormap !== "gray" ? "diverging" : value.colormap} invert={value.invert} />
      <Slider label="Brightness" min={-0.5} max={0.5} step={0.01} value={value.brightness} onChange={(v) => set("brightness", v)} format={(v) => v.toFixed(2)} />
      <Slider label="Contrast" min={0.2} max={4} step={0.05} value={value.contrast} onChange={(v) => set("contrast", v)} format={(v) => v.toFixed(2)} />
      <Row label="Clip (percentile)" hint="Automatic limits: low and high percentile of valid pixels">
        <Select label="Clip percentiles" value={value.percentiles.join("-")}
          onChange={(v) => set("percentiles", v.split("-").map(Number) as [number, number])}
          options={[{ value: "0-100", label: "min–max" }, { value: "0.5-99.5", label: "99.5%" }, { value: "1-99", label: "99%" }, { value: "2-98", label: "98%" }, { value: "5-95", label: "95%" }]} />
      </Row>
      <Row label="Limits" hint="Per channel: one scale for all frames at the same wavelength, so real brightness changes stay visible and a bright frame at one wavelength does not set the scale of another. Detector: one scale per detector. Frame: rescale each frame.">
        <Select<LimitsMode> label="Limits mode" value={value.limitsMode} onChange={(v) => set("limitsMode", v)}
          options={[{ value: "channel", label: "per wavelength channel" }, { value: "series", label: "per detector" }, { value: "frame", label: "per frame" }]} />
      </Row>
      {showPlane && (
        <Row label="Image" hint="Final: ZODI model and residual background removed, bad pixels masked. ZODI-subtracted: only the zodiacal-light model removed (keeps diffuse emission). Calibrated: the image as delivered by the SPHEREx pipeline, only resampled onto the grid.">
          <Select<SciencePlane> label="Science plane" value={value.plane ?? "SCI"} onChange={(v) => set("plane", v)}
            options={[{ value: "SCI", label: "final (background-subtracted)" }, { value: "ZSUB", label: "ZODI-subtracted" }, { value: "RAW", label: "calibrated (original)" }]} />
        </Row>
      )}
      <Toggle label="Invert" checked={value.invert} onChange={(v) => set("invert", v)} />
      <Toggle label="Smooth pixels (bicubic)" checked={(value.interpolation ?? "smooth") === "smooth"}
        onChange={(v) => set("interpolation", v ? "smooth" : "pixels")} />
      <p className="font-mono text-[11px] text-slate-500">
        scale {limits[0].toPrecision(3)} … {limits[1].toPrecision(3)} {units ?? ""}
      </p>
      <p className="text-[11px] leading-snug text-slate-500">Display only: the pixel values (and readouts) are untouched.</p>
    </Panel>
  );
}
