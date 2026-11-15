"""Blind community review.

A reviewer opens a candidate and sees the evidence (field, observations, measurements), never
the original label nor who submitted it. They make their own assessment first; only after it is
recorded does the server reveal the consensus label and whether they agreed. One review per
candidate per person, never one's own candidate, and a minimum time with the evidence before a
review counts. Community agreement is reported as agreement, with a Wilson lower bound as the
confidence indicator; it is never presented as scientific validation.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import achievements, challenges, config, scoring
from .db import audit, j, new_id, now, uj

REASONS_AGREE = {"evidence_clear", "consistent_motion", "spectral_match", "artefact_visible", "other"}
REASONS_DISAGREE = {"insufficient_evidence", "wrong_class", "artefact_not_source", "no_motion", "motion_inconsistent",
                    "poor_data_quality", "wrong_observation", "other"}


class ReviewError(ValueError):
    pass


def wilson_lower(k: int, n: int, z: float = 1.96) -> float | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    return round((c - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d, 3)


def _own(conn, cand_id: str, uid: int) -> bool:
    return conn.execute("SELECT 1 FROM contributions WHERE candidate_id=? AND user_id=?", (cand_id, uid)).fetchone() is not None


def blind_view(conn: sqlite3.Connection, cand_id: str, uid: int) -> dict[str, Any]:
    cand = conn.execute("SELECT * FROM candidates WHERE id=?", (cand_id,)).fetchone()
    if cand is None:
        raise ReviewError("no such candidate")
    if _own(conn, cand_id, uid):
        raise ReviewError("you cannot review a candidate you contributed to")
    if conn.execute("SELECT 1 FROM reviews WHERE candidate_id=? AND reviewer_id=?", (cand_id, uid)).fetchone():
        raise ReviewError("you have already reviewed this candidate")
    inv = conn.execute("SELECT * FROM investigations WHERE id=?", (cand["investigation_id"],)).fetchone()
    conn.execute("INSERT OR IGNORE INTO review_sessions(candidate_id, user_id, opened_at) VALUES (?,?,?)", (cand_id, uid, now()))
    evid = []
    for k, r in enumerate(conn.execute("SELECT payload, obs_refs, qc FROM contributions WHERE candidate_id=? AND hidden=0 "
                                       "AND qc_status IN ('passed','passed_with_warnings') ORDER BY submitted_at", (cand_id,))):
        p = uj(r["payload"], {})
        qc = uj(r["qc"], {})
        # Strip anything that states the answer; keep the measurable evidence.
        ev = {"submission": f"Investigator {chr(65 + k % 26)}", "observations": uj(r["obs_refs"], []),
              "measurements": p.get("measurements"), "findings": p.get("findings"), "features": p.get("features"),
              "evidence_score": qc.get("evidence_score")}
        evid.append({k2: v for k2, v in ev.items() if v is not None})
    data = uj(inv["data"], {})
    data.pop("training_answer", None)
    return {"candidate_id": cand_id, "category": cand["category"], "ra": cand["ra"], "dec": cand["dec"],
            "source_name": inv["source_name"], "observations": uj(inv["obs_refs"], []),
            "catalogue": {k: data.get(k) for k in ("gaia_dr3_id", "pmra_mas_yr", "pmdec_mas_yr", "gmag", "dist_pc", "position_epoch", "atlas_index")},
            "evidence": evid, "min_seconds": config.get(conn, "review")["min_seconds_before_submit"],
            "labels": _labels(cand["category"])}


def _labels(category: str) -> list[str]:
    from .quality import CLASS_LABELS
    return {"moving": ["moved", "not_moved", "unsure"], "image_quality": ["artefact", "clean", "unsure"]}.get(category, sorted(CLASS_LABELS))


def submit(conn: sqlite3.Connection, user: sqlite3.Row, cand_id: str, body: dict[str, Any]) -> dict[str, Any]:
    uid = user["id"]
    if user["status"] != "active":
        raise ReviewError("account suspended")
    cand = conn.execute("SELECT * FROM candidates WHERE id=?", (cand_id,)).fetchone()
    if cand is None:
        raise ReviewError("no such candidate")
    if _own(conn, cand_id, uid):
        raise ReviewError("you cannot review a candidate you contributed to")
    if cand["status"] not in ("community_review", "evidence_sufficient"):
        raise ReviewError(f"candidate is {cand['status']}")
    sess = conn.execute("SELECT opened_at FROM review_sessions WHERE candidate_id=? AND user_id=?", (cand_id, uid)).fetchone()
    if sess is None:
        raise ReviewError("open the evidence before reviewing")
    cfg = config.get(conn, "review")
    opened = datetime.fromisoformat(sess["opened_at"].replace("Z", "+00:00"))
    if (datetime.now(timezone.utc) - opened).total_seconds() < cfg["min_seconds_before_submit"]:
        raise ReviewError(f"spend at least {cfg['min_seconds_before_submit']} s with the evidence before submitting")
    label = body.get("label")
    if label not in _labels(cand["category"]):
        raise ReviewError("choose one of the listed labels")
    reason = body.get("reason_code")
    assessment = {"data_quality": body.get("data_quality"), "confidence": body.get("confidence"),
                  "notes": str(body.get("notes") or "")[:1000]}
    summary = uj(cand["summary"], {})
    consensus = summary.get("consensus_label")
    agrees = consensus is not None and label == consensus
    valid = REASONS_AGREE if agrees else REASONS_DISAGREE
    if reason not in REASONS_AGREE | REASONS_DISAGREE:
        raise ReviewError("give a reason code")
    if reason not in valid:
        reason = "other"   # the reviewer chose before seeing the answer; keep it honest
    rid = new_id("REV")
    try:
        conn.execute("INSERT INTO reviews(id, candidate_id, reviewer_id, assessment, independent_label, agrees, reason_code, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (rid, cand_id, uid, j(assessment), label, int(agrees), reason, now()))
    except sqlite3.IntegrityError:
        raise ReviewError("you have already reviewed this candidate") from None
    audit(conn, uid, "review.submit", "candidate", cand_id, {"review": rid, "agrees": agrees})

    sc = config.get(conn, "scoring")
    rewards = []
    ev = scoring.award(conn, uid, "contribution", "independent_review", sc["independent_review"], f"review:{rid}",
                       category="verification", ref_type="review", ref_id=rid)
    if ev:
        rewards.append(ev)
    rewards += challenges.on_event(conn, uid, "review", None, rid)
    agreement = stats(conn, cand_id)
    _maybe_strong(conn, cand, agreement, consensus)
    unlocked = achievements.evaluate(conn, uid)
    return {"review_id": rid, "your_label": label, "revealed": {"consensus_label": consensus, "labels": summary.get("labels")},
            "agrees": agrees, "agreement": agreement, "rewards": rewards, "achievements": unlocked,
            "totals": scoring.totals(conn, uid)}


def stats(conn: sqlite3.Connection, cand_id: str) -> dict[str, Any]:
    r = conn.execute("SELECT COUNT(*) n, SUM(agrees) k FROM reviews WHERE candidate_id=?", (cand_id,)).fetchone()
    n, k = r["n"], r["k"] or 0
    return {"reviews": n, "agree": k, "disagree": n - k, "agreement": round(k / n, 3) if n else None,
            "agreement_lower_95": wilson_lower(k, n),
            "note": "Community agreement among independent reviewers. Not scientific validation."}


def _maybe_strong(conn, cand, agreement: dict, consensus: str | None) -> None:
    cfg = config.get(conn, "review")
    if consensus is None or agreement["reviews"] < cfg["strong_agreement_reviews"] or agreement["agreement"] < cfg["strong_agreement_fraction"]:
        return
    if cand["status"] == "community_review":
        conn.execute("UPDATE candidates SET status='evidence_sufficient', updated_at=? WHERE id=?", (now(), cand["id"]))
        audit(conn, None, "candidate.evidence_sufficient", "candidate", cand["id"], agreement)
    pts = config.get(conn, "scoring")["strong_agreement"]
    for c in conn.execute("SELECT id, user_id, qc FROM contributions WHERE candidate_id=? AND hidden=0 "
                          "AND qc_status IN ('passed','passed_with_warnings')", (cand["id"],)).fetchall():
        if uj(c["qc"], {}).get("label") == consensus:
            scoring.award(conn, c["user_id"], "contribution", "strong_agreement", pts, f"strong:{c['id']}",
                          category=cand["category"], ref_type="contribution", ref_id=c["id"], decay=False)
            achievements.evaluate(conn, c["user_id"])


def candidate_public(conn: sqlite3.Connection, cand_id: str) -> dict[str, Any] | None:
    c = conn.execute("SELECT * FROM candidates WHERE id=?", (cand_id,)).fetchone()
    if not c:
        return None
    d = dict(c)
    d["summary"] = uj(d["summary"], {})
    d["agreement"] = stats(conn, cand_id)
    d["scientific_reviews"] = [dict(s) for s in conn.execute(
        "SELECT s.id, s.outcome, s.reviewed_at, s.simulated, s.external_ref, o.name AS organisation FROM scientific_reviews s "
        "JOIN review_orgs o ON o.id=s.org_id WHERE s.candidate_id=? ORDER BY s.reviewed_at", (cand_id,))]
    return d


__all__ = ["blind_view", "submit", "stats", "candidate_public", "ReviewError", "wilson_lower"]
