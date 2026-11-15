"""Submission pipeline: record -> automated QA -> status -> candidate -> rewards.

All of it runs inside one transaction. The client never sends points: the server decides
what a submission is worth from the QA result and the configured scoring rules, and each
reward has an idempotency key so a retried request cannot pay twice.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from typing import Any

from . import achievements, challenges, config, investigations, quality, scoring
from .db import audit, j, new_id, now, uj

PASSED = ("passed", "passed_with_warnings")


class SubmitError(ValueError):
    pass


def history(conn: sqlite3.Connection, cid: str, field: str, frm: str | None, to: str, actor: str, note: str | None = None) -> None:
    conn.execute("INSERT INTO contribution_history(contribution_id, at, field, from_status, to_status, actor, note) VALUES (?,?,?,?,?,?,?)",
                 (cid, now(), field, frm, to, actor, note))


def _dedupe_key(inv_id: str) -> str:
    return hashlib.sha256(inv_id.encode()).hexdigest()[:24]


def _candidate_for(conn: sqlite3.Connection, inv: sqlite3.Row) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM candidates WHERE investigation_id=?", (inv["id"],)).fetchone()
    if row:
        return row
    cid = new_id("CAND")
    conn.execute("INSERT INTO candidates(id, investigation_id, category, source_ref, ra, dec, status, summary, created_at, updated_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)", (cid, inv["id"], inv["category"], inv["source_ref"], inv["ra"], inv["dec"],
                                                   "collecting", "{}", now(), now()))
    conn.execute("UPDATE investigations SET candidate_id=? WHERE id=?", (cid, inv["id"]))
    return conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()


def refresh_candidate(conn: sqlite3.Connection, cand_id: str) -> dict[str, Any]:
    """Recompute the candidate's consensus label from passed, visible contributions."""
    rows = conn.execute("SELECT qc FROM contributions WHERE candidate_id=? AND qc_status IN ('passed','passed_with_warnings') AND hidden=0",
                        (cand_id,)).fetchall()
    labels = Counter(uj(r["qc"], {}).get("label") for r in rows)
    labels.pop(None, None)
    top = labels.most_common(1)[0] if labels else (None, 0)
    summary = {"n_contributions": len(rows), "labels": dict(labels), "consensus_label": top[0],
               "consensus_fraction": round(top[1] / sum(labels.values()), 3) if labels else None}
    cand = conn.execute("SELECT * FROM candidates WHERE id=?", (cand_id,)).fetchone()
    status = cand["status"]
    if status == "collecting" and rows:
        status = "community_review"
        investigations.create_verification(conn, cand)
    conn.execute("UPDATE candidates SET summary=?, status=?, updated_at=? WHERE id=?", (j(summary), status, now(), cand_id))
    return summary


def submit(conn: sqlite3.Connection, user: sqlite3.Row, inv_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if user["status"] != "active":
        raise SubmitError("account suspended")
    inv = conn.execute("SELECT * FROM investigations WHERE id=?", (inv_id,)).fetchone()
    if inv is None:
        raise SubmitError("no such investigation")
    if inv["category"] == "verification":
        raise SubmitError("verification tasks are answered through the review flow")
    if inv["status"] != "open":
        raise SubmitError(f"investigation is {inv['status']}")
    if inv["obs_refs"] is None:
        raise SubmitError("the SPHEREx observation list for this field could not be fetched from IRSA yet; try again shortly")
    if not isinstance(payload, dict) or len(j(payload)) > 20000:
        raise SubmitError("payload must be a JSON object under 20 kB")
    uid = user["id"]
    rank_before = {"all": scoring.rank_of(conn, uid, "all"), "week": scoring.rank_of(conn, uid, "week")}
    totals_before = scoring.totals(conn, uid)

    dk = _dedupe_key(inv_id)
    duplicate = conn.execute("SELECT 1 FROM contributions WHERE user_id=? AND dedupe_key=?", (uid, dk)).fetchone() is not None
    inv_out = investigations.out(inv, include_answer=True)
    cfg = config.get(conn, "quality")
    qc = quality.run(inv_out, payload, duplicate=duplicate, cfg=cfg)

    cid = new_id("CON")
    conn.execute("INSERT INTO contributions(id, user_id, investigation_id, category, payload, obs_refs, submitted_at, qc_status, qc, training, dedupe_key) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, uid, inv_id, inv["category"], j(payload), j(qc["observations_used"]), now(), "pending", "{}",
                  inv["training"], None if duplicate else dk))
    history(conn, cid, "qc_status", None, "pending", "system", "received")
    qc_store = {k: v for k, v in qc.items() if k != "training_answer"}
    conn.execute("UPDATE contributions SET qc_status=?, qc=? WHERE id=?", (qc["status"], j(qc_store), cid))
    history(conn, cid, "qc_status", "pending", qc["status"], "qc", "; ".join(qc["errors"] + qc["missing"] + qc["warnings"])[:500] or None)
    audit(conn, uid, "contribution.submit", "contribution", cid, {"investigation": inv_id, "qc_status": qc["status"]})

    rewards: list[dict] = []
    ok = qc["status"] in PASSED
    if ok:
        conn.execute("UPDATE investigations SET n_submissions=n_submissions+1 WHERE id=?", (inv_id,))
    sc = config.get(conn, "scoring")
    part = config.get(conn, "participation")

    def pay(board, kind, pts, cat=None):
        ev = scoring.award(conn, uid, board, kind, pts, f"{kind}:{cid}", category=cat, ref_type="contribution", ref_id=cid,
                           decay=board == "contribution")
        if ev:
            rewards.append(ev)

    if ok and inv["training"]:
        correct = qc.get("training_correct")
        pay("participation", "training_correct" if correct else "training_attempt",
            part["training_correct"] if correct else part["training_attempt"])
    elif ok:
        f = sc["passed_with_warnings_factor"] if qc["status"] == "passed_with_warnings" else 1.0
        cat = inv["category"]
        pay("contribution", "valid_investigation", sc["valid_investigation"] * f, cat)
        if cat in ("classification", "spectral") and qc.get("label") not in (None, "unsure"):
            pay("contribution", "valid_classification", sc["valid_classification"] * f, cat)
        if cat == "moving" and qc.get("significant_motion"):
            pay("contribution", "possible_moving_source", sc["possible_moving_source"] * f, cat)
        data = uj(inv["data"], {})
        if data.get("class_source") == "none":
            pay("contribution", "unclassified_bonus", sc["unclassified_bonus"] * f, cat)
        if qc["high_quality"]:
            pay("contribution", "high_quality_evidence", sc["high_quality_evidence"], cat)
        cand = _candidate_for(conn, inv)
        conn.execute("UPDATE contributions SET candidate_id=? WHERE id=?", (cand["id"], cid))
        refresh_candidate(conn, cand["id"])
        rewards += challenges.on_event(conn, uid, "contribution", cat, cid, unclassified=data.get("class_source") == "none")

    unlocked = achievements.evaluate(conn, uid)
    totals_after = scoring.totals(conn, uid)
    rank_after = {"all": scoring.rank_of(conn, uid, "all"), "week": scoring.rank_of(conn, uid, "week")}
    result = {"contribution_id": cid, "qc": qc, "rewards": rewards, "achievements": unlocked,
              "totals_before": totals_before, "totals": totals_after, "rank_before": rank_before, "rank": rank_after,
              "level": scoring.level_for(conn, totals_after["total_xp"]),
              "training": bool(inv["training"])}
    if not inv["training"]:
        result.pop("training", None)
    return result


def mine(conn: sqlite3.Connection, uid: int, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT c.*, i.source_name, i.category AS inv_category, cd.status AS candidate_status, "
                        "(SELECT SUM(points) FROM score_events e WHERE e.ref_type='contribution' AND e.ref_id=c.id AND e.board='contribution') AS points "
                        "FROM contributions c JOIN investigations i ON i.id=c.investigation_id LEFT JOIN candidates cd ON cd.id=c.candidate_id "
                        "WHERE c.user_id=? ORDER BY c.submitted_at DESC LIMIT ?", (uid, limit)).fetchall()
    return [_out(conn, r) for r in rows]


def _out(conn, r) -> dict[str, Any]:
    d = dict(r)
    d["payload"] = uj(d["payload"], {})
    d["qc"] = uj(d["qc"], {})
    d["obs_refs"] = uj(d["obs_refs"], [])
    d["history"] = [dict(h) for h in conn.execute("SELECT at, field, from_status, to_status, actor, note FROM contribution_history "
                                                   "WHERE contribution_id=? ORDER BY id", (r["id"],))]
    d["reviews"] = conn.execute("SELECT COUNT(*) FROM reviews WHERE candidate_id=?", (r["candidate_id"],)).fetchone()[0] if r["candidate_id"] else 0
    d["scientific_reviews"] = [dict(s) for s in conn.execute(
        "SELECT s.outcome, s.reviewed_at, s.simulated, o.name AS organisation FROM scientific_reviews s JOIN review_orgs o ON o.id=s.org_id "
        "WHERE s.candidate_id=? AND (s.contribution_id IS NULL OR s.contribution_id=?)", (r["candidate_id"], r["id"]))] if r["candidate_id"] else []
    d.pop("dedupe_key", None)
    return d


def get(conn: sqlite3.Connection, cid: str) -> dict[str, Any] | None:
    r = conn.execute("SELECT c.*, i.source_name, cd.status AS candidate_status FROM contributions c JOIN investigations i ON i.id=c.investigation_id "
                     "LEFT JOIN candidates cd ON cd.id=c.candidate_id WHERE c.id=?", (cid,)).fetchone()
    return _out(conn, r) if r else None
