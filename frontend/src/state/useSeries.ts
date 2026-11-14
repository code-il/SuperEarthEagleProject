import { useEffect, useMemo, useRef, useState } from "react";
import type { ExploreParams } from "../lib/api";
import { api, loadPlanes } from "../lib/api";
import type { ImagePlane, ProcessResponse, ProcessedObservation, SearchResponse } from "../types";

/** One frame of the time series as the viewer uses it. */
export interface Frame extends ProcessedObservation {
  /** Position in the full, time-sorted series. */
  seriesIndex: number;
  /** Visit cluster (frames separated by > 20 d start a new epoch). */
  epoch: number;
  /** Predicted target position at this frame's time (named targets with proper motion). */
  predicted?: [number, number];
}

export type LoadStatus = "idle" | "searching" | "processing" | "ready" | "error";

export interface SeriesState {
  status: LoadStatus;
  error: string | null;
  message: string;
  processed: ProcessResponse | null;
  search: SearchResponse | null;
  frames: Frame[];
  /** Seconds since the request started (for the loading indicator). */
  elapsed: number;
}

export const EPOCH_GAP_DAYS = 20;

export function paramsKey(p: ExploreParams | null): string {
  return p ? JSON.stringify(p) : "";
}

export function buildFrames(proc: ProcessResponse, search: SearchResponse | null): Frame[] {
  const pred = new Map<string, [number, number]>();
  for (const o of search?.observations ?? [])
    if (o.target_predicted_ra != null && o.target_predicted_dec != null) pred.set(o.observation_id, [o.target_predicted_ra, o.target_predicted_dec]);
  const sorted = [...proc.observations].sort((a, b) => a.mjd - b.mjd || a.detector - b.detector);
  let epoch = 0;
  return sorted.map((o, i) => {
    if (i > 0 && o.mjd - sorted[i - 1].mjd > EPOCH_GAP_DAYS) epoch++;
    return { ...o, seriesIndex: i, epoch, predicted: pred.get(o.observation_id) };
  });
}

/** Search (Person 1) + process (Person 2) a field; both requests run in parallel. */
export function useSeries(params: ExploreParams | null): SeriesState {
  const [state, setState] = useState<SeriesState>({ status: "idle", error: null, message: "", processed: null, search: null, frames: [], elapsed: 0 });
  const key = paramsKey(params);

  useEffect(() => {
    if (!params) return;
    const ctl = new AbortController();
    const t0 = Date.now();
    const timer = setInterval(() => setState((s) => ({ ...s, elapsed: (Date.now() - t0) / 1000 })), 500);
    setState({ status: "searching", error: null, message: "Searching the SPHEREx archive…", processed: null, search: null, frames: [], elapsed: 0 });
    const searchP = api.search(params, ctl.signal).catch(() => null);
    searchP.then((s) => {
      if (ctl.signal.aborted) return;
      setState((st) => (st.status === "searching"
        ? { ...st, status: "processing", search: s, message: s ? `Found ${s.count} images. Downloading cutouts and aligning them on one grid…` : "Aligning images on one grid…" }
        : st));
    });
    api.process(params, undefined, ctl.signal)
      .then(async (proc) => {
        const s = await searchP;
        if (ctl.signal.aborted) return;
        const frames = buildFrames(proc, s);
        setState((st) => ({
          ...st, status: "ready", processed: proc, search: s, frames, error: null,
          message: frames.length ? "" : "No SPHEREx images cover this position with the current filters.",
        }));
      })
      .catch((e: Error) => {
        if (ctl.signal.aborted) return;
        setState((st) => ({ ...st, status: "error", error: e.message, message: "" }));
      })
      .finally(() => clearInterval(timer));
    return () => {
      ctl.abort();
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return state;
}

/**
 * Load every frame's FITS in the background so playback is smooth.
 * Frames near `priority` load first. Returns planes by seriesIndex.
 */
export function usePlanes(frames: Frame[], priority: number) {
  const [planes, setPlanes] = useState<Map<number, ImagePlane>>(new Map());
  const [failed, setFailed] = useState<Map<number, string>>(new Map());
  const prioRef = useRef(priority);
  prioRef.current = priority;
  const framesKey = useMemo(() => frames.map((f) => f.fits_url).join("|"), [frames]);

  useEffect(() => {
    setPlanes(new Map());
    setFailed(new Map());
    if (!frames.length) return;
    const ctl = new AbortController();
    const p = Math.max(0, prioRef.current);
    const order = frames.map((_, i) => i).sort((a, b) => Math.abs(a - p) - Math.abs(b - p));
    let pending = new Map<number, ImagePlane>();
    let flushTimer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      flushTimer = null;
      const batch = pending;
      pending = new Map();
      if (!ctl.signal.aborted) setPlanes((m) => new Map([...m, ...batch]));
    };
    loadPlanes(frames.map((f) => f.fits_url), (i, plane, err) => {
      if (plane) {
        pending.set(i, plane);
        if (!flushTimer) flushTimer = setTimeout(flush, 80);
      } else if (err && !ctl.signal.aborted) setFailed((m) => new Map(m).set(i, err.message));
    }, { signal: ctl.signal, order, concurrency: 6 });
    return () => {
      ctl.abort();
      if (flushTimer) clearTimeout(flushTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [framesKey]);

  return { planes, failed };
}
