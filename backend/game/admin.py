"""Administration and moderation. Every change is written to the audit log."""
from __future__ import annotations

import sqlite3
from typing import Any

from . import achievements, config, contributions, scoring
from .db import audit, j, new_id, now, uj

OUTCOMES = ("expert_classification", "accepted_candidate", "known_object_confirmed", "recognised", "not_validated")


def users(conn: sqlite3.Connection, q: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT id, username, display_name, role, status, created_at, org_id FROM users"
    args: tuple = ()
    if q:
        sql += " WHERE username LIKE ? OR display_name LIKE ?"
        args = (f"%{q}%", f"%{q}%")
    return [{**dict(r), **scoring.totals(conn, r["id"])} for r in conn.execute(sql + " ORDER BY id LIMIT 500", args)]


def set_user(conn: sqlite3.Connection, actor: int, uid: int, role: str | None = None, status: str | None = None,
             org_id: int | None = None) -> None:
    if uid == actor and (role not in (None, "admin") or status == "suspended"):
        raise ValueError("you cannot demote or suspend yourself")
    if role is not None:
        if role not in ("player", "reviewer", "admin"):
            raise ValueError("bad role")
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    if status is not None:
        if status not in ("active", "suspended"):
            raise ValueError("bad status")
        conn.execute("UPDATE users SET status=? WHERE id=?", (status, uid))
        if status == "suspended":
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    if org_id is not None:
        conn.execute("UPDATE users SET org_id=? WHERE id=?", (org_id or None, uid))
    audit(conn, actor, "admin.user", "user", uid, {"role": role, "status": status, "org_id": org_id})


def moderate_contribution(conn: sqlite3.Connection, actor: int, cid: str, action: str, reason: str) -> None:
    """hide / unhide / reject. Rejecting reverses that contribution's points with a ledger entry."""
    c = conn.execute("SELECT * FROM contributions WHERE id=?", (cid,)).fetchone()
    if c is None:
        raise ValueError("no such contribution")
    if not reason:
        raise ValueError("give a reason")
    if action in ("hide", "unhide"):
        conn.execute("UPDATE contributions SET hidden=? WHERE id=?", (int(action == "hide"), cid))
        contributions.history(conn, cid, "hidden", str(c["hidden"]), str(int(action == "hide")), f"admin:{actor}", reason)
    elif action == "reject":
        conn.execute("UPDATE contributions SET qc_status='rejected' WHERE id=?", (cid,))
        contributions.history(conn, cid, "qc_status", c["qc_status"], "rejected", f"admin:{actor}", reason)
        for board in ("contribution", "participation"):
            p = conn.execute("SELECT SUM(points) FROM score_events WHERE ref_type='contribution' AND ref_id=? AND board=?",
                             (cid, board)).fetchone()[0] or 0
            if p:
                scoring.award(conn, c["user_id"], board, "reversal", -p, f"reversal:{board}:{cid}", category=c["category"],
                              ref_type="contribution", ref_id=cid, note=f"moderation: {reason[:80]}", decay=False)
    else:
        raise ValueError("action must be hide, unhide or reject")
    if c["candidate_id"]:
        contributions.refresh_candidate(conn, c["candidate_id"])
    audit(conn, actor, f"moderation.{action}", "contribution", cid, {"reason": reason})


def set_investigation(conn: sqlite3.Connection, actor: int, iid: str, status: str) -> None:
    if status not in ("open", "closed", "disabled"):
        raise ValueError("bad status")
    conn.execute("UPDATE investigations SET status=? WHERE id=?", (status, iid))
    audit(conn, actor, "admin.investigation", "investigation", iid, {"status": status})


def set_achievement(conn: sqlite3.Connection, actor: int, key: str, enabled: bool) -> None:
    conn.execute("UPDATE achievements SET enabled=? WHERE key=?", (int(enabled), key))
    audit(conn, actor, "admin.achievement", "achievement", key, {"enabled": enabled})


def create_org(conn: sqlite3.Connection, actor: int, name: str, identifier: str | None, notes: str | None) -> int:
    if not name.strip():
        raise ValueError("name required")
    cur = conn.execute("INSERT INTO review_orgs(name, identifier, notes, created_at) VALUES (?,?,?,?)",
                       (name.strip()[:120], identifier, notes, now()))
    audit(conn, actor, "admin.org.create", "org", cur.lastrowid, {"name": name})
    return cur.lastrowid


def orgs(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT o.*, (SELECT COUNT(*) FROM users u WHERE u.org_id=o.id) AS reviewers FROM review_orgs o ORDER BY name")]


def assign(conn: sqlite3.Connection, actor: int, cand_id: str, org_id: int) -> None:
    conn.execute("INSERT OR REPLACE INTO review_assignments(candidate_id, org_id, status, assigned_at, assigned_by) VALUES (?,?,?,?,?)",
                 (cand_id, org_id, "assigned", now(), actor))
    conn.execute("UPDATE candidates SET status='awaiting_scientific_review', updated_at=? WHERE id=? AND status IN ('community_review','evidence_sufficient')",
                 (now(), cand_id))
    audit(conn, actor, "admin.assign", "candidate", cand_id, {"org_id": org_id})


def record_scientific_review(conn: sqlite3.Connection, actor: int, body: dict[str, Any]) -> str:
    """Enter an external review outcome. ``simulated`` must be stated explicitly; real records need
    an organisation, a reviewer identifier and an external reference."""
    cand = conn.execute("SELECT * FROM candidates WHERE id=?", (body.get("candidate_id"),)).fetchone()
    if cand is None:
        raise ValueError("no such candidate")
    if body.get("outcome") not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    if "simulated" not in body:
        raise ValueError("state whether this record is simulated demonstration data")
    sim = bool(body["simulated"])
    org = conn.execute("SELECT * FROM review_orgs WHERE id=?", (body.get("org_id"),)).fetchone()
    if org is None:
        raise ValueError("unknown organisation")
    if not sim and not (body.get("reviewer_identifier") and body.get("external_ref")):
        raise ValueError("a real review needs the reviewer identifier and an external reference (e.g. a report or ticket id)")
    if body.get("contribution_id") and not conn.execute("SELECT 1 FROM contributions WHERE id=? AND candidate_id=?",
                                                        (body["contribution_id"], cand["id"])).fetchone():
        raise ValueError("that contribution does not belong to the candidate")
    sid = new_id("SCI")
    conn.execute("INSERT INTO scientific_reviews(id, candidate_id, contribution_id, org_id, reviewer_user_id, reviewer_identifier, outcome, "
                 "comments, evidence, external_ref, reviewed_at, simulated, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (sid, cand["id"], body.get("contribution_id"), org["id"], body.get("reviewer_user_id"),
                  body.get("reviewer_identifier") or "simulated", body["outcome"], body.get("comments"), j(body.get("evidence") or []),
                  body.get("external_ref"), body.get("reviewed_at") or now(), int(sim), actor, now()))
    if not sim:
        conn.execute("UPDATE candidates SET status='scientifically_reviewed', updated_at=? WHERE id=?", (now(), cand["id"]))
        new = "not_validated" if body["outcome"] == "not_validated" else "validated"
        for c in conn.execute("SELECT id, user_id, science_status FROM contributions WHERE candidate_id=? AND (? IS NULL OR id=?)",
                              (cand["id"], body.get("contribution_id"), body.get("contribution_id"))).fetchall():
            conn.execute("UPDATE contributions SET science_status=? WHERE id=?", (new, c["id"]))
            contributions.history(conn, c["id"], "science_status", c["science_status"], new, f"external:{org['name']}", body.get("external_ref"))
            achievements.evaluate(conn, c["user_id"])
        conn.execute("UPDATE review_assignments SET status='completed' WHERE candidate_id=? AND org_id=?", (cand["id"], org["id"]))
    audit(conn, actor, "scientific_review.record", "candidate", cand["id"], {"id": sid, "outcome": body["outcome"], "simulated": sim})
    return sid


def candidates(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    sql = ("SELECT c.*, (SELECT COUNT(*) FROM reviews r WHERE r.candidate_id=c.id) AS n_reviews, "
           "(SELECT COUNT(*) FROM contributions x WHERE x.candidate_id=c.id) AS n_contributions FROM candidates c")
    args: tuple = ()
    if status:
        sql += " WHERE c.status=?"
        args = (status,)
    return [{**dict(r), "summary": uj(r["summary"], {})} for r in conn.execute(sql + " ORDER BY c.updated_at DESC LIMIT 500", args)]


def metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    q = lambda s: conn.execute(s).fetchone()[0] or 0  # noqa: E731
    by_status = {r[0]: r[1] for r in conn.execute("SELECT qc_status, COUNT(*) FROM contributions GROUP BY qc_status")}
    return {"users": q("SELECT COUNT(*) FROM users"), "suspended": q("SELECT COUNT(*) FROM users WHERE status='suspended'"),
            "contributions_by_qc": by_status, "reviews": q("SELECT COUNT(*) FROM reviews"),
            "candidates_by_status": {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM candidates GROUP BY status")},
            "open_investigations": {r[0]: r[1] for r in conn.execute("SELECT category, COUNT(*) FROM investigations WHERE status='open' GROUP BY category")},
            "data_unavailable": q("SELECT COUNT(*) FROM investigations WHERE status='data_unavailable'"),
            "score_events": q("SELECT COUNT(*) FROM score_events")}


def audit_log(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    return [{**dict(r), "details": uj(r["details"], {})} for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]


def put_config(conn: sqlite3.Connection, actor: int, key: str, value: Any) -> None:
    config.put(conn, key, value, actor)
