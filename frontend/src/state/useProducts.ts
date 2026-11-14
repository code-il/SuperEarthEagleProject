/** Backend-computed products for the comparison modes, and catalogue / analysis overlays. */
import { useEffect, useState } from "react";
import type { CompareParams, ExploreParams } from "../lib/api";
import { api, loadPlane } from "../lib/api";
import type { AnalysisResponse, CatalogResponse, CompareResponse, Grid, ImagePlane, LightCurve, Spectrum, StackProduct } from "../types";
import type { CompareOptions, ShowPlane, StackOptions } from "../components/ModePanel";

interface Async<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/** Difference / ratio from POST /api/compare, then its FITS plane (DIFF/RATIO or SIG). */
export function useCompare(params: ExploreParams | null, enabled: boolean, mode: "difference" | "ratio", aIds: string[], bIds: string[], opts: CompareOptions) {
  const [res, setRes] = useState<Async<CompareResponse>>({ data: null, loading: false, error: null });
  const [plane, setPlane] = useState<ImagePlane | null>(null);
  const key = JSON.stringify([params, enabled, mode, aIds, bIds, opts.psfMatching, opts.matchBackground, opts.matchWavelength, opts.smoothFwhm]);

  useEffect(() => {
    if (!enabled || !params || !aIds.length || !bIds.length) {
      setRes({ data: null, loading: false, error: null });
      return;
    }
    const ctl = new AbortController();
    setRes((r) => ({ ...r, loading: true, error: null }));
    const t = setTimeout(() => {
      const c: CompareParams = {
        a: { observation_ids: aIds }, b: { observation_ids: bIds }, mode,
        psf_matching: opts.psfMatching, match_background: opts.matchBackground, match_wavelength: opts.matchWavelength,
        smooth_fwhm_arcsec: opts.smoothFwhm, stack_method: "median",
      };
      api.compare(params, c, ctl.signal)
        .then((d) => !ctl.signal.aborted && setRes({ data: d, loading: false, error: null }))
        .catch((e: Error) => !ctl.signal.aborted && setRes({ data: null, loading: false, error: e.message }));
    }, 350);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    setPlane(null);
    if (!res.data) return;
    let dead = false;
    const ext = opts.show === "significance" ? ["SIG"] : ["DIFF", "RATIO"];
    loadPlane(res.data.fits_url, ext).then((p) => !dead && setPlane(p)).catch(() => !dead && setPlane(null));
    return () => {
      dead = true;
    };
  }, [res.data, opts.show]);

  return { ...res, plane, show: opts.show as ShowPlane };
}

/** Stacks from POST /api/process with stack_by. */
export function useStacks(params: ExploreParams | null, enabled: boolean, opts: StackOptions) {
  const [res, setRes] = useState<Async<StackProduct[]>>({ data: null, loading: false, error: null });
  const key = JSON.stringify([params, enabled, opts]);
  useEffect(() => {
    if (!enabled || !params) return;
    const ctl = new AbortController();
    setRes({ data: null, loading: true, error: null });
    api.process(params, { by: opts.groupBy, method: opts.method }, ctl.signal)
      .then((d) => !ctl.signal.aborted && setRes({ data: [...(d.stacks ?? [])].sort((a, b) => a.mjd_mean - b.mjd_mean || a.wavelength_mean - b.wavelength_mean), loading: false, error: null }))
      .catch((e: Error) => !ctl.signal.aborted && setRes({ data: null, loading: false, error: e.message }));
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return res;
}

export function usePlane(url: string | null | undefined, ext: string[] = ["SCI"]) {
  const [plane, setPlane] = useState<ImagePlane | null>(null);
  const k = `${url}#${ext.join()}`;
  useEffect(() => {
    setPlane(null);
    if (!url) return;
    let dead = false;
    loadPlane(url, ext).then((p) => !dead && setPlane(p)).catch(() => !dead && setPlane(null));
    return () => {
      dead = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [k]);
  return plane;
}

/** Catalogue cone search around the grid (only when the overlay is switched on). */
export function useCatalog(name: "gaia" | "catwise", grid: Grid | null, enabled: boolean) {
  const [res, setRes] = useState<Async<CatalogResponse>>({ data: null, loading: false, error: null });
  const key = grid ? `${name}:${grid.center_ra.toFixed(5)}:${grid.center_dec.toFixed(5)}:${grid.fov_deg}` : "";
  const [fetchedKey, setFetchedKey] = useState("");
  useEffect(() => {
    if (!enabled || !grid || key === fetchedKey) return;
    const ctl = new AbortController();
    setRes({ data: null, loading: true, error: null });
    // Half-diagonal of the square field, plus margin for fast movers.
    const radius = Math.min(0.5, grid.fov_deg * 0.75 + 0.005);
    api.catalog(name, grid.center_ra, grid.center_dec, radius, ctl.signal)
      .then((d) => {
        if (ctl.signal.aborted) return;
        setRes({ data: d, loading: false, error: null });
        setFetchedKey(key);
      })
      .catch((e: Error) => !ctl.signal.aborted && setRes({ data: null, loading: false, error: e.message }));
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, key]);
  return res;
}

export function useAnalysis(grid: Grid | null, target?: string) {
  const [res, setRes] = useState<Async<AnalysisResponse>>({ data: null, loading: false, error: null });
  const key = grid ? `${grid.center_ra}:${grid.center_dec}:${grid.fov_deg}:${target ?? ""}` : "";
  useEffect(() => {
    if (!grid) return;
    const ctl = new AbortController();
    setRes({ data: null, loading: true, error: null });
    api.analysis(grid.center_ra, grid.center_dec, grid.fov_deg, ctl.signal, target)
      .then((d) => !ctl.signal.aborted && setRes({ data: d, loading: false, error: null }))
      .catch((e: Error) => !ctl.signal.aborted && setRes({ data: null, loading: false, error: e.message }));
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return res;
}

/** Light curve + spectra of one analysed source (fetched when the user selects it). */
export function useSourceProducts(id: string | null, grid: Grid | null, target?: string) {
  const [res, setRes] = useState<Async<{ light_curve: LightCurve | null; spectra: Spectrum[] }>>({ data: null, loading: false, error: null });
  const key = id && grid ? `${id}:${grid.center_ra}:${grid.center_dec}:${grid.fov_deg}:${target ?? ""}` : "";
  useEffect(() => {
    if (!id || !grid) {
      setRes({ data: null, loading: false, error: null });
      return;
    }
    const ctl = new AbortController();
    setRes({ data: null, loading: true, error: null });
    api.sourceProducts(id, grid.center_ra, grid.center_dec, grid.fov_deg, target, ctl.signal)
      .then((d) => !ctl.signal.aborted && setRes({ data: d, loading: false, error: null }))
      .catch((e: Error) => !ctl.signal.aborted && setRes({ data: null, loading: false, error: e.message }));
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return res;
}
