import { useEffect, useRef, useState } from "react";
import type { GalaxyScene } from "./galaxyScene";
import { type NbodyChunk, trackEnd, trackFromChunk } from "./nbodyTrack";

export type OrbitEngineMode = "kepler" | "nbody";

export interface NbodyState {
  status: "idle" | "loading" | "ok" | "refused" | "not_converged" | "error";
  problems: string[];
  notes: string[];
  end: number | null;
}

const IDLE: NbodyState = { status: "idle", problems: [], notes: [], end: null };
const CHUNK_DAYS = 120;

/**
 * Loads N-body tracks for the open planetary system when the N-body engine is chosen, in
 * 120-day chunks, fetching the next chunk 20 simulated days (20 s) before the current one ends.
 * When the backend refuses or cannot reproduce the archive, nothing is installed and the
 * scene keeps the Kepler orbits; the reasons are returned for the legend.
 */
export function useNbody(engine: React.RefObject<GalaxyScene | null>, sel: number, orbitJd: number | null) {
  const [mode, setMode] = useState<OrbitEngineMode>("kepler");
  const [state, setState] = useState<NbodyState>(IDLE);
  const busy = useRef(false);
  const forSel = useRef(-1);

  const load = (i: number, fromJd: number) => {
    if (busy.current) return;
    busy.current = true;
    setState((s) => ({ ...s, status: s.status === "ok" ? "ok" : "loading" }));
    fetch(`/api/orbits/system/${i}/positions?from_jd=${fromJd}&days=${CHUNK_DAYS}`)
      .then((r) => (r.ok ? (r.json() as Promise<NbodyChunk>) : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((c) => {
        if (forSel.current !== i) return;
        if (c.status !== "ok") {
          engine.current?.setNbody(i, null);
          setState({ status: c.status, problems: c.problems ?? [], notes: c.notes ?? [], end: null });
          return;
        }
        const t = trackFromChunk(c);
        engine.current?.setNbody(i, t);
        setState({ status: "ok", problems: [], notes: c.notes, end: trackEnd(t) });
      })
      .catch((e: Error) => { if (forSel.current === i) setState({ ...IDLE, status: "error", problems: [e.message] }); })
      .finally(() => { busy.current = false; });
  };

  // New system or engine switched: reset, and request a first chunk in N-body mode.
  useEffect(() => {
    forSel.current = sel;
    busy.current = false;
    engine.current?.setNbody(sel, null);
    setState(IDLE);
  }, [sel, mode, engine]);

  useEffect(() => {
    if (mode !== "nbody" || sel < 0 || orbitJd == null) return;
    if (state.status === "idle") load(sel, orbitJd - 1);
    else if (state.status === "ok" && state.end != null && orbitJd > state.end - 20) load(sel, orbitJd - 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, sel, orbitJd, state.status, state.end]);

  return { mode, setMode, state };
}
