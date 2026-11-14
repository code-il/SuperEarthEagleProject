"""Temporal matching across epochs (section B).

Strategy, chosen for SPHEREx's cadence (a field is visited every ~6
months; each visit is a burst of frames over hours to weeks, each at a
different wavelength):

1. **Visit stacks.** Frames of one visit (epoch, from Person 2's
   ``assign_epochs``) are combined with Person 2's inverse-variance
   ``stack_group``. Single frames often miss faint sources (WISE 0855
   reaches 5 sigma in 9 of 45 D6 frames); a stack gains ~sqrt(N). Stellar
   motion within a visit is small (Barnard's Star: 0.03"/day).
2. **Detection** on each stack and on each frame (``detect_sources``).
3. **Registration check.** Stack detections are matched to Gaia DR3 stars
   with small proper motion; their mean offset is the frame-to-catalogue
   registration error of that visit. It is removed when measured with at
   least ``MIN_REG_STARS`` stars, and its uncertainty (or a default floor)
   is carried into every position error.
4. **Linking** visit to visit: each existing track predicts where its
   source should be (linear fit once it has two visits, otherwise "here,
   +/- the fastest motion we allow"), and detections are assigned by
   minimum-cost matching (Hungarian algorithm, cost = distance / gate).
   The cost favours the smallest motion consistent with the data.
5. **Intra-visit tracklets.** Frame detections not explained by any stack
   source are searched for >= 3 detections moving linearly at
   solar-system rates (>= 0.5"/h; stars move < 0.002"/h).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

from backend.image_processing.pipeline import assign_epochs, stack_group

from .detection import detect_sources
from .motion import DAYS_PER_YEAR, calculate_angular_motion, predict_position, separation_arcsec, to_tangent

log = logging.getLogger(__name__)

MAX_PM_ARCSEC_YR = 12.0
"""Fastest stellar motion the linker allows between visits (Barnard's Star: 10.4"/yr)."""
LINK_FLOOR_ARCSEC = 1.5
DEFAULT_REGISTRATION_ARCSEC = 0.5
"""Registration uncertainty assumed when a visit has too few Gaia reference stars."""
MIN_REG_STARS = 3
REG_MATCH_ARCSEC = 3.0
REG_MAX_PM_MAS_YR = 200.0
SSO_MIN_RATE_ARCSEC_H = 0.5
SSO_MAX_RATE_ARCSEC_H = 500.0
STACK_ASSOC_ARCSEC = 4.0
SATURATED_HALO_ARCSEC = 30.0
SATURATED_VISIT_FLOOR_ARCSEC = 0.3
"""Systematic per-visit floor for saturated cores (bloom / clipped-PSF asymmetry does not average out)."""


# --------------------------------------------------------------------------
# Visit stacks
# --------------------------------------------------------------------------
def build_visit_stacks(observations: Sequence[Any]) -> list[SimpleNamespace]:
    """One inverse-variance stack per visit (epoch), with RAW stacked too."""
    epochs = assign_epochs(observations)
    groups: dict[int, list[Any]] = {}
    for o in observations:
        groups.setdefault(epochs[o.observation_id], []).append(o)
    stacks = []
    for e in sorted(groups):
        members = groups[e]
        by_det: dict[Any, list[Any]] = {}
        for m in members:
            by_det.setdefault(m.detector, []).append(m)
        for det, mem in sorted(by_det.items(), key=lambda kv: kv[0] or 0):
            try:
                res, summ = stack_group(mem, "weighted" if all(m.variance is not None for m in mem) else "median")
            except ValueError as exc:
                log.warning("visit %s stack failed: %s", e, exc)
                continue
            raws = np.array([m.raw for m in mem], dtype=float)
            with np.errstate(all="ignore"):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    raw = np.nanmedian(raws, axis=0)
            mjds = np.array([m.mjd for m in mem if m.mjd is not None])
            fw = [m.psf_fwhm for m in mem if m.psf_fwhm]
            flag_defs = next((m.flag_definitions for m in mem if m.flag_definitions), {})
            flags = np.bitwise_or.reduce(np.array([m.flags for m in mem]).astype(np.int64), axis=0)
            stacks.append(SimpleNamespace(
                observation_id=f"visit{e}_D{det}", epoch=e, members=[m.observation_id for m in mem],
                mjd=float(mjds.mean()) if len(mjds) else None,
                mjd_range=[float(mjds.min()), float(mjds.max())] if len(mjds) else None,
                observation_time=None, wavelength=summ.get("wavelength_mean"), detector=det,
                psf_fwhm=max(fw) if fw else None, data=res.data, variance=res.variance,
                mask=(~res.valid).astype(np.uint8), flags=flags, raw=raw, wcs=mem[0].wcs,
                flag_definitions=flag_defs, n_frames=len(mem),
                releases=sorted({m.data_release for m in mem if m.data_release}),
                wavelength_range=[float(min(m.wavelength for m in mem if m.wavelength)),
                                  float(max(m.wavelength for m in mem if m.wavelength))]
                if any(m.wavelength for m in mem) else None))
    return stacks


# --------------------------------------------------------------------------
# Registration against Gaia
# --------------------------------------------------------------------------
def estimate_registration(detections: list[dict[str, Any]], reference: list[dict[str, Any]] | None,
                          mjd: float) -> dict[str, Any]:
    """Mean (detected - catalogue) offset for low-proper-motion Gaia stars.

    Returns ``{"dra_arcsec", "ddec_arcsec", "sigma_arcsec", "n_stars", "applied"}``.
    The offset is applied only with >= ``MIN_REG_STARS`` stars; otherwise the
    default uncertainty floor is reported and nothing is shifted.
    """
    from .crossmatch import propagate_catalogue_source

    out = {"dra_arcsec": 0.0, "ddec_arcsec": 0.0, "sigma_arcsec": DEFAULT_REGISTRATION_ARCSEC,
           "n_stars": 0, "applied": False, "reference": "Gaia DR3"}
    if not reference or not detections:
        out["reason"] = "no reference catalogue" if not reference else "no detections"
        return out
    refs = [r for r in reference if r.get("pm_ra_cosdec_mas_yr") is not None
            and math.hypot(r["pm_ra_cosdec_mas_yr"], r["pm_dec_mas_yr"]) < REG_MAX_PM_MAS_YR]
    if not refs:
        out["reason"] = "no low-motion reference stars"
        return out
    rpos = np.array([propagate_catalogue_source(r, mjd) for r in refs])
    offs, used = [], set()
    for d in detections:
        if d["quality_flags"] and set(d["quality_flags"]) - {"blended"}:
            continue
        sep = separation_arcsec(d["ra"], d["dec"], rpos[:, 0], rpos[:, 1])
        k = int(np.argmin(sep))
        if sep[k] < REG_MATCH_ARCSEC and k not in used:
            used.add(k)
            xi, eta = to_tangent(np.array([d["ra"]]), np.array([d["dec"]]), rpos[k, 0], rpos[k, 1])
            offs.append((float(xi[0]), float(eta[0]), math.hypot(d["ra_err_arcsec"], d["dec_err_arcsec"]) / math.sqrt(2)))
    out["n_stars"] = len(offs)
    if len(offs) < MIN_REG_STARS:
        out["reason"] = f"only {len(offs)} matched reference star(s)"
        return out
    o = np.array(offs)
    dx, dy = float(np.median(o[:, 0])), float(np.median(o[:, 1]))
    scatter = float(np.sqrt(np.mean((o[:, 0] - dx) ** 2 + (o[:, 1] - dy) ** 2) / 2))
    sig = max(scatter / math.sqrt(len(o)) * 1.25, 0.1)  # 1.25: median vs mean efficiency
    out.update(dra_arcsec=dx, ddec_arcsec=dy, sigma_arcsec=sig, scatter_arcsec=scatter, applied=True)
    return out


def apply_registration(detections: list[dict[str, Any]], reg: dict[str, Any]) -> None:
    """Shift positions by -offset and add the registration error (in place)."""
    from .motion import from_tangent

    for d in detections:
        if reg.get("applied"):
            ra, dec = from_tangent(-reg["dra_arcsec"], -reg["ddec_arcsec"], d["ra"], d["dec"])
            d["ra"], d["dec"] = float(ra), float(dec)
        d["registration_err_arcsec"] = reg["sigma_arcsec"]
        d["registration_applied_gaia"] = bool(reg.get("applied"))
        d["ra_err_total_arcsec"] = math.hypot(d["ra_err_arcsec"], reg["sigma_arcsec"])
        d["dec_err_total_arcsec"] = math.hypot(d["dec_err_arcsec"], reg["sigma_arcsec"])


# --------------------------------------------------------------------------
# Linking across visits
# --------------------------------------------------------------------------
@dataclass
class Track:
    id: str
    points: list[dict[str, Any]] = field(default_factory=list)
    """One stack detection per visit."""

    def motion(self) -> dict[str, Any]:
        return calculate_angular_motion([(p["ra"], p["dec"]) for p in self.points], [p["mjd"] for p in self.points],
                                        [(p["ra_err_total_arcsec"], p["dec_err_total_arcsec"]) for p in self.points])

    def predict(self, mjd: float) -> tuple[float, float, float]:
        """(ra, dec, 1-sigma uncertainty in arcsec) at ``mjd``."""
        last = self.points[-1]
        sig_last = math.hypot(last["ra_err_total_arcsec"], last["dec_err_total_arcsec"]) / math.sqrt(2)
        span = (self.points[-1]["mjd"] - self.points[0]["mjd"]) / DAYS_PER_YEAR
        if len(self.points) >= 2 and span > 0.1:
            m = self.motion()
            if m.get("ra_at_ref") is not None:
                ra, dec = predict_position(m, mjd)
                dt = abs(mjd - m["ref_mjd"]) / DAYS_PER_YEAR
                pe = m.get("position_err_at_ref_arcsec") or [sig_last, sig_last]
                sig = math.hypot(math.hypot(*pe) / math.sqrt(2), (m.get("pm_err_mas_yr") or 0) / 1000 * dt)
                return ra, dec, sig
        dt = abs(mjd - last["mjd"]) / DAYS_PER_YEAR
        return last["ra"], last["dec"], -(sig_last + MAX_PM_ARCSEC_YR * dt)  # negative: "free motion" gate


def link_detections(visits: list[dict[str, Any]], max_pm_arcsec_yr: float = MAX_PM_ARCSEC_YR) -> list[Track]:
    """Link per-visit detections into tracks. ``visits``: [{"mjd", "detections"}], any order."""
    tracks: list[Track] = []
    for v in sorted(visits, key=lambda v: v["mjd"]):
        dets = v["detections"]
        if not dets:
            continue
        if not tracks:
            tracks = [Track(f"S{i + 1:03d}", [d]) for i, d in enumerate(dets)]
            continue
        cost = np.full((len(tracks), len(dets)), 1e6)
        for i, t in enumerate(tracks):
            ra, dec, sig = t.predict(v["mjd"])
            for j, d in enumerate(dets):
                sd = math.hypot(d["ra_err_total_arcsec"], d["dec_err_total_arcsec"]) / math.sqrt(2)
                if sig >= 0:  # predicted from a motion fit
                    gate = 3 * math.hypot(sig, sd) + LINK_FLOOR_ARCSEC
                else:  # one visit so far: allow any motion up to the limit
                    dt = abs(v["mjd"] - t.points[-1]["mjd"]) / DAYS_PER_YEAR
                    gate = 3 * math.hypot(-sig - max_pm_arcsec_yr * dt, sd) + max_pm_arcsec_yr * dt + LINK_FLOOR_ARCSEC
                sep = float(separation_arcsec(ra, dec, d["ra"], d["dec"]))
                if sep <= gate:
                    cost[i, j] = sep / gate
        rows, cols = linear_sum_assignment(cost)
        used = set()
        for i, j in zip(rows, cols):
            if cost[i, j] < 1e6:
                tracks[i].points.append(dets[j])
                used.add(j)
        for j, d in enumerate(dets):
            if j not in used:
                tracks.append(Track(f"S{len(tracks) + 1:03d}", [d]))
    return tracks


# --------------------------------------------------------------------------
# Intra-visit tracklets (fast movers)
# --------------------------------------------------------------------------
def find_tracklets(detections: list[dict[str, Any]], min_points: int = 3,
                   min_rate: float = SSO_MIN_RATE_ARCSEC_H, max_rate: float = SSO_MAX_RATE_ARCSEC_H,
                   tol_arcsec: float = 2.5) -> list[dict[str, Any]]:
    """Groups of >= ``min_points`` frame detections on a straight line at constant rate.

    Brute force over pairs (fine for the tens of orphan detections in a
    SPHEREx field). Each detection is used in at most one tracklet.
    """
    pts = sorted([d for d in detections if d.get("mjd") is not None], key=lambda d: d["mjd"])
    if len(pts) < min_points:
        return []
    ra0, dec0 = pts[0]["ra"], pts[0]["dec"]
    xi, eta = to_tangent(np.array([p["ra"] for p in pts]), np.array([p["dec"] for p in pts]), ra0, dec0)
    t = np.array([p["mjd"] for p in pts]) * 24.0  # hours
    frame = [p["observation_id"] for p in pts]
    free = np.ones(len(pts), bool)
    out = []
    while True:
        best = None
        for i in range(len(pts)):
            if not free[i]:
                continue
            for j in range(i + 1, len(pts)):
                if not free[j] or frame[i] == frame[j]:
                    continue
                dt = t[j] - t[i]
                if dt <= 0:
                    continue
                vx, vy = (xi[j] - xi[i]) / dt, (eta[j] - eta[i]) / dt
                rate = math.hypot(vx, vy)
                if not (min_rate <= rate <= max_rate) or math.hypot(xi[j] - xi[i], eta[j] - eta[i]) < 3 * tol_arcsec:
                    continue
                px, py = xi[i] + vx * (t - t[i]), eta[i] + vy * (t - t[i])
                inl = free & (np.hypot(xi - px, eta - py) < tol_arcsec)
                # one detection per frame
                sel, seen = [], set()
                for k in np.flatnonzero(inl):
                    if frame[k] not in seen:
                        seen.add(frame[k])
                        sel.append(k)
                if len(sel) >= min_points and (best is None or len(sel) > len(best[0])):
                    best = (sel, vx, vy)
        if best is None:
            break
        sel, vx, vy = best
        free[sel] = False
        tt = t[sel]
        a = np.vstack([np.ones_like(tt), tt - tt.mean()]).T
        cx = np.linalg.lstsq(a, xi[sel], rcond=None)[0]
        cy = np.linalg.lstsq(a, eta[sel], rcond=None)[0]
        res = np.hypot(xi[sel] - a @ cx, eta[sel] - a @ cy)
        out.append({"detections": [pts[k] for k in sel], "n": len(sel),
                    "rate_arcsec_h": float(math.hypot(cx[1], cy[1])),
                    "rate_ra_cosdec_arcsec_h": float(cx[1]), "rate_dec_arcsec_h": float(cy[1]),
                    "pa_deg": float(math.degrees(math.atan2(cx[1], cy[1])) % 360),
                    "rms_arcsec": float(np.sqrt(np.mean(res ** 2))),
                    "duration_h": float(tt.max() - tt.min()),
                    "mjd_mid": float(tt.mean() / 24.0)})
    return out


def _empirical_visit_errors(tracks: list[Track], frame_dets: list[dict[str, Any]], min_frames: int = 5) -> None:
    """Replace a visit position's formal error by the scatter of its frame detections when there are enough.

    The PSF fit's formal error is unreliable for saturated cores (fitted on
    RAW) and for undersampled data in general. With >= ``min_frames`` frame
    detections the standard error of their robust mean is used instead
    (plus a 0.1" floor), which is an honest measure of repeatability.
    """
    by: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for d in frame_dets:
        if d.get("track_id") and "fit_failed" not in d["quality_flags"]:
            by.setdefault((d["track_id"], d["epoch"]), []).append(d)
    for t in tracks:
        for p in t.points:
            fr = by.get((t.id, p["epoch"]), [])
            if len(fr) < min_frames:
                continue
            xi, eta = to_tangent(np.array([d["ra"] for d in fr]), np.array([d["dec"] for d in fr]), p["ra"], p["dec"])
            mad = lambda v: 1.4826 * float(np.median(np.abs(v - np.median(v))))
            floor = SATURATED_VISIT_FLOOR_ARCSEC if {"masked_core", "saturated_core_raw_fit"} & set(p["quality_flags"]) else 0.1
            sx = math.hypot(1.25 * mad(xi) / math.sqrt(len(fr)), floor)
            sy = math.hypot(1.25 * mad(eta) / math.sqrt(len(fr)), floor)
            p["formal_ra_err_arcsec"], p["formal_dec_err_arcsec"] = p["ra_err_arcsec"], p["dec_err_arcsec"]
            p["ra_err_arcsec"], p["dec_err_arcsec"] = sx, sy
            p["error_source"] = f"scatter of {len(fr)} frame detections"
            reg = p.get("registration_err_arcsec", DEFAULT_REGISTRATION_ARCSEC)
            p["ra_err_total_arcsec"], p["dec_err_total_arcsec"] = math.hypot(sx, reg), math.hypot(sy, reg)


def calibrate_registration_floor(tracks: list[Track], registrations: list[dict[str, Any]],
                                 min_visits: int = 3) -> dict[str, Any]:
    """Measure the visit-to-visit registration error from stationary field sources.

    Reference sources: >= ``min_visits`` visits, no saturation flags, no
    significant motion. Their scatter about their own mean position,
    minus the measurement errors (in quadrature), is the registration error
    common to all sources. It replaces the default floor in every position
    error (bounded to 0.1-1.0"). Needs >= 4 degrees of freedom, otherwise
    the default floor stays.
    """
    refs, chi_terms, meas2, dof = [], 0.0, [], 0
    for t in tracks:
        if len(t.points) < min_visits or any({"masked_core", "saturated_core_raw_fit", "edge"} & set(p["quality_flags"])
                                             for p in t.points):
            continue
        m = t.motion()
        if (m.get("motion_sigma") or 0) > 3 or (m.get("pm_total_mas_yr") or 0) > 500:
            continue
        xi, eta = to_tangent(np.array([p["ra"] for p in t.points]), np.array([p["dec"] for p in t.points]),
                             t.points[0]["ra"], t.points[0]["dec"])
        ex = np.array([p["ra_err_arcsec"] for p in t.points])
        ey = np.array([p["dec_err_arcsec"] for p in t.points])
        for v, e in ((xi, ex), (eta, ey)):
            w = 1 / e ** 2
            chi_terms += float(np.sum((v - np.sum(w * v) / w.sum()) ** 2))
            meas2 += list(e ** 2)
            dof += len(v) - 1
        refs.append(t.id)
    default = registrations[0]["sigma_arcsec"] if registrations else DEFAULT_REGISTRATION_ARCSEC
    out = {"method": "default floor", "sigma_arcsec": default, "n_reference_sources": len(refs),
           "reference_sources": refs, "dof": dof}
    if dof < 4:
        out["reason"] = "fewer than 4 degrees of freedom from stationary field sources"
        return out
    var = chi_terms / dof - float(np.mean(meas2))  # per-axis scatter beyond the measurement errors
    sig = float(min(max(math.sqrt(max(var, 0.0)), 0.1), 1.0))
    out.update(method="scatter of stationary field sources", sigma_arcsec=round(sig, 3),
               raw_rms_arcsec=round(math.sqrt(chi_terms / dof), 3))
    for t in tracks:
        for p in t.points:
            if p.get("registration_applied_gaia"):
                continue
            p["registration_err_arcsec"] = sig
            p["ra_err_total_arcsec"] = math.hypot(p["ra_err_arcsec"], sig)
            p["dec_err_total_arcsec"] = math.hypot(p["dec_err_arcsec"], sig)
    return out


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def match_sources(observations: Sequence[Any], *, reference_stars: list[dict[str, Any]] | None = None,
                  nsigma: float = 5.0, max_pm_arcsec_yr: float = MAX_PM_ARCSEC_YR) -> dict[str, Any]:
    """Detect and link sources through a time series of processed observations.

    ``observations``: Person 2's ``ProcessedObservation`` objects on one grid
    (e.g. ``process_series(...).observations``). ``reference_stars``: Gaia
    DR3 sources (``catalogs.cone_search("gaia", ...)["sources"]``) for the
    registration check; without them the default registration floor is used.

    Returns ``{"visits", "tracks", "frame_detections", "orphans", "tracklets"}``.
    Each track holds one registration-corrected stack detection per visit;
    every frame detection is also attached to the track it belongs to.
    """
    observations = [o for o in observations if o.mjd is not None]
    stacks = build_visit_stacks(observations)
    visits = []
    for st in stacks:
        dets = detect_sources(st, nsigma=nsigma)
        for d in dets:
            d["epoch"] = st.epoch
            d["stack"] = True
            d["n_frames"] = st.n_frames
        reg = estimate_registration(dets, reference_stars, st.mjd)
        apply_registration(dets, reg)
        visits.append({"epoch": st.epoch, "detector": st.detector, "mjd": st.mjd, "mjd_range": st.mjd_range,
                       "n_frames": st.n_frames, "wavelength_range": st.wavelength_range, "releases": st.releases,
                       "observation_id": st.observation_id, "members": st.members,
                       "registration": reg, "detections": dets, "stack": st})
    # A track should not mix detectors: link per detector.
    tracks: list[Track] = []
    for det in sorted({v["detector"] for v in visits}, key=lambda d: d or 0):
        tr = link_detections([v for v in visits if v["detector"] == det], max_pm_arcsec_yr)
        for t in tr:
            t.id = f"S{len(tracks) + 1:03d}"
            tracks.append(t)

    # Frame detections, corrected with their visit's registration and attached to tracks.
    epoch_of = assign_epochs(observations)
    reg_of = {(v["epoch"], v["detector"]): v["registration"] for v in visits}
    frame_dets: list[dict[str, Any]] = []
    for o in observations:
        ds = detect_sources(o, nsigma=nsigma)
        e = epoch_of[o.observation_id]
        for d in ds:
            d["epoch"] = e
        apply_registration(ds, reg_of.get((e, o.detector), {"sigma_arcsec": DEFAULT_REGISTRATION_ARCSEC}))
        frame_dets += ds
    orphans = []
    for d in frame_dets:
        best, best_sep = None, None
        for t in tracks:
            p = next((q for q in t.points if q["epoch"] == d["epoch"]), None)
            if p is None:
                continue
            sep = float(separation_arcsec(p["ra"], p["dec"], d["ra"], d["dec"]))
            gate = STACK_ASSOC_ARCSEC + 3 * math.hypot(d["ra_err_total_arcsec"], p["ra_err_total_arcsec"])
            if sep < gate and (best_sep is None or sep < best_sep):
                best, best_sep = t, sep
        if best is not None:
            d["track_id"] = best.id
        else:
            orphans.append(d)
    _empirical_visit_errors(tracks, frame_dets)
    reg_cal = calibrate_registration_floor(tracks, [v["registration"] for v in visits])

    # Fast-mover search: clean orphans only, away from saturated stars (halo artefacts).
    sat_pos = [(p["ra"], p["dec"]) for t in tracks for p in t.points
               if {"masked_core", "saturated_core_raw_fit"} & set(p["quality_flags"])]
    clean = [d for d in orphans if not d["quality_flags"] and (d.get("snr") or 0) >= nsigma
             and all(separation_arcsec(d["ra"], d["dec"], a, b) > SATURATED_HALO_ARCSEC for a, b in sat_pos)]
    tracklets = []
    for e in sorted({d["epoch"] for d in clean}):
        for tl in find_tracklets([d for d in clean if d["epoch"] == e]):
            tl["epoch"] = e
            tl["id"] = f"T{len(tracklets) + 1:03d}"
            tracklets.append(tl)
    return {"visits": visits, "tracks": tracks, "frame_detections": frame_dets,
            "orphans": orphans, "tracklets": tracklets, "registration_calibration": reg_cal}
