"""The two leaderboards and global community progress.

A. Global Contribution Leaderboard: computed live from the contribution board of the score
   ledger (so it updates the moment a submission passes QA); all-time, weekly, monthly, and per
   category. Rank change is measured against the previous UTC day's snapshot.
B. Scientifically Validated Leaderboard: computed only from external scientific review records
   entered by an administrator. Simulated demo records are kept apart and always labelled; the
   real board may be empty, and says so.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import demo, scoring
from .db import now

CATS = ("moving", "classification", "image_quality", "spectral", "verification")
VALIDATED_OUTCOMES = ("expert_classification", "accepted_candidate", "known_object_confirmed", "recognised")


def _name(r) -> str:
    return r["display_name"] or r["username"]


def snapshot(conn: sqlite3.Connection, view: str, rows: list) -> None:
    """Store today's ranks once per UTC day (first view of the day)."""
    day = datetime.now(timezone.utc).date().isoformat()
    if conn.execute("SELECT 1 FROM leaderboard_snapshots WHERE day=? AND view=? LIMIT 1", (day, view)).fetchone():
        return
    conn.executemany("INSERT OR IGNORE INTO leaderboard_snapshots(day, view, user_id, rank) VALUES (?,?,?,?)",
                     [(day, view, r["user_id"], k) for k, r in enumerate(rows, 1)])


def contribution(conn: sqlite3.Connection, period: str = "all", category: str | None = None, uid: int | None = None,
                 limit: int = 50) -> dict[str, Any]:
    if category is not None and category not in CATS:
        raise ValueError("unknown category")
    rows = scoring.ranking(conn, period, category)
    view = f"{period}:{category or '*'}"
    yday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    prev = {r["user_id"]: r["rank"] for r in conn.execute("SELECT user_id, rank FROM leaderboard_snapshots WHERE day=? AND view=?", (yday, view))}
    snapshot(conn, view, rows)

    def entry(k, r):
        uid_ = r["user_id"]
        is_demo = demo.is_demo(r["username"])
        if is_demo:   # demo players have ledger events only (no contributions or reviews in the science pipeline)
            ev = lambda kind: conn.execute("SELECT COUNT(*) FROM score_events WHERE user_id=? AND kind=?" + (" AND category=?" if category else ""),  # noqa: E731
                                           (uid_, kind, category) if category else (uid_, kind)).fetchone()[0]
            valid, reviews = ev("valid_investigation"), ev("independent_review")
        else:
            valid = conn.execute("SELECT COUNT(*) FROM contributions WHERE user_id=? AND training=0 AND qc_status IN ('passed','passed_with_warnings')"
                                 + (" AND category=?" if category else ""), (uid_, category) if category else (uid_,)).fetchone()[0]
            reviews = conn.execute("SELECT COUNT(*) FROM reviews WHERE reviewer_id=?", (uid_,)).fetchone()[0]
        st = conn.execute("SELECT current FROM streaks WHERE user_id=?", (uid_,)).fetchone()
        tot = scoring.totals(conn, uid_)
        return {"rank": k, "user_id": uid_, "name": _name(r), "points": round(r["points"], 2), "valid_contributions": valid,
                "reviews": reviews, "streak": st["current"] if st else 0, "level": scoring.level_for(conn, tot["total_xp"])["level"],
                "rank_change": (prev[uid_] - k) if uid_ in prev else None, "demo": is_demo}

    top = [entry(k, r) for k, r in enumerate(rows[:limit], 1)]
    me = near = None
    if uid is not None:
        pos = next((k for k, r in enumerate(rows, 1) if r["user_id"] == uid), None)
        if pos is not None:
            me = entry(pos, rows[pos - 1])
            near = [entry(k, rows[k - 1]) for k in range(max(1, pos - 2), min(len(rows), pos + 2) + 1)]
    has_demo = any(demo.is_demo(r["username"]) for r in rows)
    return {"board": "global_contribution", "period": period, "category": category, "updated_at": now(),
            "total_ranked": len(rows), "entries": top, "me": me, "nearby": near, "includes_demo": has_demo,
            "note": "Points for contributions that passed automated quality checks and for independent reviews. "
                    "This measures participation in the project, not scientific validation."
                    + (" Rows marked demo are sample players seeded for a demonstration." if has_demo else "")}


def validated(conn: sqlite3.Connection, simulated: bool = False) -> dict[str, Any]:
    q = ("SELECT c.user_id, u.username, u.display_name, COUNT(DISTINCT s.id) AS n, "
         "GROUP_CONCAT(DISTINCT s.outcome) AS outcomes, GROUP_CONCAT(DISTINCT o.name) AS orgs, MAX(s.reviewed_at) AS last "
         "FROM scientific_reviews s JOIN contributions c ON c.candidate_id=s.candidate_id AND (s.contribution_id IS NULL OR s.contribution_id=c.id) "
         "JOIN users u ON u.id=c.user_id JOIN review_orgs o ON o.id=s.org_id "
         "WHERE s.simulated=? AND s.outcome IN (%s) AND c.qc_status IN ('passed','passed_with_warnings') AND c.hidden=0 "
         "GROUP BY c.user_id ORDER BY n DESC, last ASC" % ",".join("?" * len(VALIDATED_OUTCOMES)))
    rows = conn.execute(q, (int(simulated), *VALIDATED_OUTCOMES)).fetchall()
    return {"board": "scientifically_validated", "simulated": simulated,
            "entries": [{"rank": k, "user_id": r["user_id"], "name": _name(r), "validated_contributions": r["n"],
                         "outcomes": sorted(set((r["outcomes"] or "").split(","))), "organisations": sorted(set((r["orgs"] or "").split(","))),
                         "last_reviewed": r["last"]} for k, r in enumerate(rows, 1)],
            "note": ("SIMULATED DEMONSTRATION DATA: not real scientific reviews." if simulated else
                     "Only contributions accepted by an external scientific review, recorded by an administrator with the "
                     "organisation and reference. Community agreement and automated checks never place anyone here.")}


def progress(conn: sqlite3.Connection) -> dict[str, Any]:
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0] or 0  # noqa: E731
    passed = "qc_status IN ('passed','passed_with_warnings') AND training=0"
    totals = {
        "investigators": q("SELECT COUNT(*) FROM users WHERE status='active' AND username NOT LIKE 'demo\\_%' ESCAPE '\\'"),
        "investigations_completed": q(f"SELECT COUNT(*) FROM contributions WHERE {passed}"),
        "sources_classified": q(f"SELECT COUNT(DISTINCT investigation_id) FROM contributions WHERE {passed} AND category='classification'"),
        "uncharted_sources_classified": q(f"SELECT COUNT(DISTINCT c.investigation_id) FROM contributions c JOIN investigations i ON i.id=c.investigation_id "
                                          f"WHERE c.{passed.replace('training', 'c.training')} AND c.category='classification' AND json_extract(i.data,'$.class_source')='none'"),
        "moving_measurements": q(f"SELECT COUNT(*) FROM contributions WHERE {passed} AND category='moving'"),
        "spectra_examined": q(f"SELECT COUNT(*) FROM contributions WHERE {passed} AND category='spectral'"),
        "artefact_checks": q(f"SELECT COUNT(*) FROM contributions WHERE {passed} AND category='image_quality'"),
        "independent_reviews": q("SELECT COUNT(*) FROM reviews"),
        "candidates_in_review": q("SELECT COUNT(*) FROM candidates WHERE status='community_review'"),
        "candidates_evidence_sufficient": q("SELECT COUNT(*) FROM candidates WHERE status IN ('evidence_sufficient','awaiting_scientific_review')"),
        "scientifically_reviewed": q("SELECT COUNT(*) FROM candidates WHERE status='scientifically_reviewed'"),
        "external_validations": q("SELECT COUNT(*) FROM scientific_reviews WHERE simulated=0 AND outcome!='not_validated'"),
    }
    try:
        from .investigations import catalog
        c = catalog()
        totals["uncharted_sources_in_atlas"] = int((c["cls"] == 10).sum())
    except Exception:  # noqa: BLE001
        totals["uncharted_sources_in_atlas"] = None
    goals = [("investigations_completed", [100, 1000, 10000, 100000]), ("independent_reviews", [50, 500, 5000]),
             ("uncharted_sources_classified", [100, 1000, 10000])]
    milestones = []
    for key, steps in goals:
        v = totals[key]
        nxt = next((s for s in steps if s > v), None)
        milestones.append({"metric": key, "value": v, "reached": [s for s in steps if s <= v], "next": nxt,
                           "progress": None if nxt is None else round(v / nxt, 4)})
    by_day = [dict(r) for r in conn.execute(
        f"SELECT substr(submitted_at,1,10) AS day, COUNT(*) AS n FROM contributions WHERE {passed} "
        "GROUP BY day ORDER BY day DESC LIMIT 30")]
    return {"totals": totals, "milestones": milestones, "daily": list(reversed(by_day)), "updated_at": now()}
