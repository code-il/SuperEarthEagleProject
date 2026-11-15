"""Investigation pool and allocation.

Every investigation points at a real source in the Atlas catalogue (Gaia DR3 / SIMBAD) and at
the real SPHEREx images of its position, found with the app's existing archive search
(``backend.data_acquisition.search_observations``). Nothing is invented:
  * classification: sources with no class in any catalogue (the Atlas's "uncharted" objects);
    plus training tasks on catalogued stars whose SIMBAD class is known (clearly flagged; they
    earn participation XP only);
  * moving: stars with large Gaia proper motion (their real motion is the reference);
  * spectral: catalogued brown dwarfs and nearby M dwarfs (SPHEREx spectra measured on demand
    by the existing /api/galaxy/spectrum endpoint);
  * image_quality: the brightest stars, whose fields are where SPHEREx artefacts (ghosts,
    saturation, bleed) actually occur;
  * verification: created from player candidates that need independent review.
An investigation whose position has no SPHEREx images is marked ``data_unavailable``.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import numpy as np

from . import config
from .db import j, new_id, now, uj

CATEGORIES = ("moving", "classification", "image_quality", "spectral", "verification")
CLASS_KEYS = ["ob", "a", "f", "g", "k", "m", "bd", "wd", "giant", "yso", "unknown"]

INSTRUCTIONS = {
    "classification": (
        "Open the time-lapse viewer and the SPHEREx all-sky image for this position. Look at how bright the "
        "source is across wavelengths (0.75–5 µm) and compare it with its neighbours. Choose the class that "
        "best fits, or 'not a real source' if it looks like an artefact, and say how confident you are. "
        "Note which observations you examined."),
    "moving": (
        "Open the time-lapse viewer in Blink or Difference mode. Pick two observations from different dates, "
        "measure the source's position in each (the Analysis panel gives measured positions), and give your "
        "positional uncertainty. Decide whether the source moved between the two dates."),
    "spectral": (
        "Measure or open the SPHEREx spectrum of this source. Look for absorption bands: water (≈1.4 and 1.9 µm), "
        "methane (≈2.2 and 3.3 µm), carbon monoxide (≈2.3 and 4.6 µm). Record which features you see and "
        "what class the spectrum suggests."),
    "image_quality": (
        "Bright stars produce artefacts. Step through this field's observations and flag any artefact you find "
        "(ghost, saturation, bleed trail, cosmic ray, satellite trail, detector edge), with the observation and "
        "position. If a frame is clean, say so."),
}

_catalog_loader: Callable[[], dict[str, np.ndarray]] | None = None
_catalog: dict[str, np.ndarray] | None = None
_search: Callable[..., list] | None = None


def set_sources(catalog_loader: Callable[[], dict] | None = None, search: Callable[..., list] | None = None) -> None:
    """Swap the catalogue/archive (tests use small offline fixtures)."""
    global _catalog_loader, _catalog, _search
    _catalog_loader, _catalog, _search = catalog_loader, None, search


def catalog() -> dict[str, np.ndarray]:
    global _catalog
    if _catalog is None:
        if _catalog_loader is not None:
            _catalog = _catalog_loader()
        else:
            from backend.galaxy.catalog import load_catalog
            _catalog = load_catalog()
    return _catalog


def _search_fn():
    if _search is not None:
        return _search
    from backend.data_acquisition import search_observations
    return search_observations


def _f(a, i):
    v = a[i]
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) or (hasattr(v, "dtype") and v.dtype.kind == "f" and not np.isfinite(v)) else (v.item() if hasattr(v, "item") else v)


def _record(c: dict, i: int) -> dict[str, Any]:
    name = str(c["name"][i]) or (f"Gaia DR3 {int(c['source_id'][i])}" if int(c["source_id"][i]) else f"Atlas object {i}")
    return {"atlas_index": int(i), "gaia_dr3_id": str(int(c["source_id"][i])) if int(c["source_id"][i]) else None,
            "name": name, "class_id": int(c["cls"][i]), "class_key": CLASS_KEYS[int(c["cls"][i])],
            "class_source": str(c["src"][i]), "class_confidence": _f(c["prob"], i),
            "pmra_mas_yr": _f(c["pmra"], i), "pmdec_mas_yr": _f(c["pmdec"], i), "gmag": _f(c["gmag"], i),
            "dist_pc": _f(c["dist"], i), "otype": str(c["otype"][i]) or None, "sptype": str(c["sptype"][i]) or None,
            "position_epoch": "J2016.0 (Gaia DR3)" if int(c["source_id"][i]) else "J2000 (SIMBAD)"}


def _candidates_for(category: str, training: bool) -> np.ndarray:
    c = catalog()
    n = len(c["ra"])
    dist = np.nan_to_num(c["dist"], nan=1e9)
    if category == "classification":
        if training:
            m = (c["src"] == "catalogue") & (c["cls"] < 10) & (np.nan_to_num(c["gmag"], nan=99) < 12)
        else:
            m = c["cls"] == 10
        idx = np.nonzero(m)[0]
        return idx[np.argsort(dist[idx], kind="stable")]
    if category == "moving":
        pm = np.hypot(np.nan_to_num(c["pmra"]), np.nan_to_num(c["pmdec"]))
        idx = np.nonzero(pm > 500)[0]
        return idx[np.argsort(-pm[idx], kind="stable")]
    if category == "spectral":
        m = (c["src"] == "catalogue") & ((c["cls"] == 6) | ((c["cls"] == 5) & (dist < 10)))
        idx = np.nonzero(m)[0]
        return idx[np.argsort(dist[idx], kind="stable")]
    if category == "image_quality":
        g = np.nan_to_num(c["gmag"], nan=99)
        idx = np.nonzero(g < 4)[0]
        return idx[np.argsort(g[idx], kind="stable")]
    return np.arange(0, dtype=int) if n else np.arange(0)


def _difficulty(category: str, rec: dict, training: bool) -> int:
    if training:
        return 1
    if category == "moving":
        pm = float(np.hypot(rec["pmra_mas_yr"] or 0, rec["pmdec_mas_yr"] or 0))
        return 1 if pm > 5000 else 2 if pm > 2000 else 3
    if category == "classification":
        return 3
    if category == "spectral":
        return 2 if rec["class_key"] == "bd" else 3
    return 1 if (rec["gmag"] or 9) < 2 else 2


RESERVED = "json_extract(data, '$.daily_reserved') IS NULL"   # SQL: not a Daily Survey anchor


def ensure_pool(conn: sqlite3.Connection, category: str, extra_real: int = 0) -> int:
    """Top up the open pool for a category from the catalogue. Returns how many were added.
    ``extra_real`` raises the target for real (non-training) tasks, so the Daily Survey can open
    more of the sky when the existing pool has already been classified enough times."""
    if category == "verification":
        return 0
    pool = config.get(conn, "pool")
    want = pool["open_per_category"]
    added = 0
    for training in ((False, True) if category == "classification" else (False,)):
        target = int(round(want * (pool["training_fraction"] if training else 1 - (pool["training_fraction"] if category == "classification" else 0))))
        if not training:
            target += max(0, int(extra_real))
        have = conn.execute(f"SELECT COUNT(*) FROM investigations WHERE category=? AND status='open' AND training=? AND {RESERVED}",
                            (category, int(training))).fetchone()[0]
        if have >= target:
            continue
        c = catalog()
        used = {r[0] for r in conn.execute("SELECT source_ref FROM investigations WHERE category=?", (category,))}
        for i in _candidates_for(category, training):
            if have >= target:
                break
            ref = f"atlas:{int(i)}"
            if ref in used:
                continue
            rec = _record(c, int(i))
            data = {**rec}
            if training:
                data["training_answer"] = rec["class_key"]
            conn.execute("INSERT OR IGNORE INTO investigations(id, category, source_ref, source_name, ra, dec, difficulty, status, training, "
                         "instructions, data, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (new_id("INV"), category, ref, rec["name"], float(c["ra"][i]), float(c["dec"][i]),
                          _difficulty(category, rec, training), "open", int(training), INSTRUCTIONS[category], j(data), now()))
            have += 1
            added += 1
    return added


def compact_obs(r: Any) -> dict[str, Any]:
    g = (lambda k: getattr(r, k, None)) if not isinstance(r, dict) else r.get
    return {"id": g("observation_id"), "time": g("observation_time"), "band": g("band"), "detector": g("detector"),
            "wavelength_min": g("wavelength_min"), "wavelength_max": g("wavelength_max"), "release": g("data_release"),
            "mjd": g("mjd_start"), "quality_flags": list(g("quality_flags") or [])}


def needs_observations(inv: sqlite3.Row, force: bool = False) -> bool:
    if inv["category"] == "verification" or (inv["obs_refs"] is not None and not force):
        return False
    if inv["obs_checked_at"] and not force:
        last = datetime.fromisoformat(inv["obs_checked_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - last < timedelta(minutes=10):
            return False
    return True


def fetch_observations(inv: sqlite3.Row) -> list[dict] | None:
    """Query the archive (no database lock held). None means the archive could not be reached."""
    try:
        recs = _search_fn()(inv["ra"], inv["dec"], 0.1)
    except Exception:  # noqa: BLE001  (archive unreachable: try again later, nothing recorded as fact)
        return None
    return sorted((compact_obs(r) for r in recs), key=lambda o: o["time"] or "")


def store_observations(conn: sqlite3.Connection, iid: str, refs: list[dict] | None) -> sqlite3.Row:
    inv = conn.execute("SELECT * FROM investigations WHERE id=?", (iid,)).fetchone()
    if refs is None:
        conn.execute("UPDATE investigations SET obs_checked_at=? WHERE id=?", (now(), iid))
    else:
        status = inv["status"] if refs else "data_unavailable"
        conn.execute("UPDATE investigations SET obs_refs=?, obs_checked_at=?, status=? WHERE id=?", (j(refs), now(), status, iid))
    return conn.execute("SELECT * FROM investigations WHERE id=?", (iid,)).fetchone()


def attach_observations(conn: sqlite3.Connection, inv: sqlite3.Row, force: bool = False) -> sqlite3.Row:
    """Find the real SPHEREx images of this position (cached by the archive layer)."""
    if not needs_observations(inv, force):
        return inv
    return store_observations(conn, inv["id"], fetch_observations(inv))


def out(inv: sqlite3.Row, include_answer: bool = False) -> dict[str, Any]:
    d = dict(inv)
    data = uj(d.pop("data"), {})
    if not include_answer:
        data.pop("training_answer", None)
        if inv["category"] == "classification" and not inv["training"]:
            pass  # unclassified: nothing to hide
        elif inv["category"] in ("classification",):
            data.pop("class_key", None); data.pop("class_id", None); data.pop("sptype", None); data.pop("otype", None)
            data.pop("class_confidence", None)
    d["data"] = data
    refs = uj(d.pop("obs_refs"), None)
    d["observations"] = refs
    d["observation_count"] = None if refs is None else len(refs)
    d["kind"] = "training" if inv["training"] else "real"
    return d


def discover(conn: sqlite3.Connection, uid: int | None, category: str | None = None, limit: int = 24) -> list[dict[str, Any]]:
    """Open investigations for this player, most useful first:
    candidates needing verification, then uncharted sources, then the least-investigated."""
    cats = [category] if category else [c for c in CATEGORIES]
    for c in cats:
        ensure_pool(conn, c)
    done = set()
    if uid is not None:
        done = {r[0] for r in conn.execute("SELECT DISTINCT investigation_id FROM contributions WHERE user_id=? AND qc_status!='duplicate'", (uid,))}
        done |= {r[0] for r in conn.execute("SELECT i.id FROM investigations i JOIN reviews r ON r.candidate_id=i.candidate_id "
                                            "WHERE i.category='verification' AND r.reviewer_id=?", (uid,))}
        own = {r[0] for r in conn.execute("SELECT DISTINCT candidate_id FROM contributions WHERE user_id=? AND candidate_id IS NOT NULL", (uid,))}
    else:
        own = set()
    wanted = config.get(conn, "review")["reviews_wanted"]
    rows = conn.execute(
        "SELECT i.*, (SELECT COUNT(*) FROM reviews r WHERE r.candidate_id=i.candidate_id) AS n_reviews FROM investigations i "
        "WHERE i.status='open' AND json_extract(i.data, '$.daily_reserved') IS NULL AND i.category IN (%s)" % ",".join("?" * len(cats)),
        cats).fetchall()

    def priority(r) -> tuple:
        data = uj(r["data"], {})
        if r["category"] == "verification":
            return (0, r["n_reviews"], r["created_at"])
        if data.get("class_source") == "none":
            return (1, r["n_submissions"], r["difficulty"])
        return (2 + r["training"], r["n_submissions"], r["difficulty"])

    rows = [r for r in rows if r["id"] not in done and not (r["category"] == "verification" and (r["candidate_id"] in own or r["n_reviews"] >= wanted * 3))]
    rows.sort(key=priority)
    if category:
        picked = rows[:limit]
    else:  # interleave categories so every kind of task is offered
        by: dict[str, list] = {}
        for r in rows:
            by.setdefault(r["category"], []).append(r)
        picked, k = [], 0
        while len(picked) < limit and any(by.values()):
            for c in CATEGORIES:
                if by.get(c) and len(by[c]) > k:
                    picked.append(by[c][k])
            k += 1
            if k > limit:
                break
        picked = picked[:limit]
    return [{**out(r), "n_reviews": r["n_reviews"]} for r in picked]


def create_verification(conn: sqlite3.Connection, cand: sqlite3.Row) -> str:
    row = conn.execute("SELECT id FROM investigations WHERE category='verification' AND source_ref=?", (f"candidate:{cand['id']}",)).fetchone()
    if row:
        return row["id"]
    iid = new_id("INV")
    conn.execute("INSERT INTO investigations(id, category, source_ref, source_name, ra, dec, difficulty, status, training, instructions, data, "
                 "candidate_id, obs_refs, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (iid, "verification", f"candidate:{cand['id']}", f"Verify {cand['id']}", cand["ra"], cand["dec"], 2, "open", 0,
                  "Independently assess another investigator's candidate. You will make your own assessment first; "
                  "their answer is shown only after you submit.", j({"candidate_category": cand["category"]}), cand["id"], None, now()))
    return iid
