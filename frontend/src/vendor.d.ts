declare module "plotly.js-dist-min" {
  const Plotly: unknown;
  export default Plotly;
}

declare module "react-plotly.js/factory" {
  import type { ComponentType, CSSProperties } from "react";
  export interface PlotParams {
    data: Record<string, unknown>[];
    layout?: Record<string, unknown>;
    config?: Record<string, unknown>;
    style?: CSSProperties;
    className?: string;
    useResizeHandler?: boolean;
    onClick?: (e: { points: { customdata?: unknown; pointIndex: number; curveNumber: number }[] }) => void;
  }
  export default function createPlotlyComponent(plotly: unknown): ComponentType<PlotParams>;
}

declare module "aladin-lite" {
  const A: unknown;
  export default A;
}
