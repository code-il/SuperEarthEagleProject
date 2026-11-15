"""Score ledger and leaderboards.

Two boards in one append-only ledger, never mixed:
  * ``contribution``: points for useful scientific activity that passed the automated checks
    (Global Contribution Leaderboard: all-time, weekly, monthly, per category);
  * ``participation``: XP for logins, streaks, challenges and training (levels only).
The Scientifically Validated Leaderboard is not in this ledger at all: it is computed from
``scientific_reviews`` (see leaderboards.validated).

Every event has a unique ``idem_key``; awarding the same thing twice is a no-op.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import config
from .db import now


def _day_start_utc() -> str:
    d = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return d.isoformat(timespec="seconds").replace("+00:00", "Z")


def decay_factor(conn: sqlite3.Connection, user_id: int, kind: str, category: str | None) -> float:
    """Diminishing returns for repeating the same activity in one UTC day."""
    sc = config.get(conn, "scoring")
    n = conn.execute("SELECT COUNT(*) FROM score_events WHERE user_id=? AND kind=? AND IFNULL(category,'')=? AND created_at>=?",
                     (user_id, kind, category or "", _day_start_utc())).fetchone()[0]
    if n < sc["full_per_day"]:
        return 1.0
    block = (n - sc["full_per_day"]) // max(1, sc["decay_block"])
    f = sc["decay_factors"]
    return f[block] if block < len(f) else sc["floor_factor"]


def award(conn: sqlite3.Connection, user_id: int, board: str, kind: str, points: float, idem_key: str,
          category: str | None = None, ref_type: str | None = None, ref_id: str | None = None,
          note: str | None = None, decay: bool = True) -> dict | None:
    """Add one ledger event. Returns it, or None if this idem_key was already paid."""
    if conn.execute("SELECT 1 FROM score_events WHERE idem_key=?", (idem_key,)).fetchone():
        return None
    f = decay_factor(conn, user_id, kind, category) if decay else 1.0
    pts = round(points * f, 2)
    conn.execute("INSERT INTO score_events(user_id, board, kind, points, category, ref_type, ref_id, idem_key, note, created_at)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (user_id, board, kind, pts, category, ref_type, ref_id, idem_key, note, now()))
    return {"board": board, "kind": kind, "points": pts, "factor": f, "category": category, "note": note}


def window_start(period: str) -> str | None:
    t = datetime.now(timezone.utc)
    if period == "all":
        return None
    if period == "week":   # ISO week, Monday 00:00 UTC
        s = (t - timedelta(days=t.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        s = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(period)
    return s.isoformat(timespec="seconds").replace("+00:00", "Z")


def ranking(conn: sqlite3.Connection, period: str = "all", category: str | None = None) -> list[sqlite3.Row]:
    start = window_start(period)
    q = ("SELECT u.id AS user_id, u.username, u.display_name, SUM(e.points) AS points, COUNT(*) AS events, "
         "MIN(e.created_at) AS first_at FROM score_events e JOIN users u ON u.id=e.user_id "
         "WHERE e.board='contribution' AND u.status='active'")
    args: list = []
    if start:
        q += " AND e.created_at >= ?"; args.append(start)
    if category:
        q += " AND e.category = ?"; args.append(category)
    q += " GROUP BY u.id HAVING points > 0 ORDER BY points DESC, first_at ASC"
    return conn.execute(q, args).fetchall()


def rank_of(conn: sqlite3.Connection, user_id: int, period: str = "all", category: str | None = None) -> int | None:
    for k, r in enumerate(ranking(conn, period, category), start=1):
        if r["user_id"] == user_id:
            return k
    return None


def totals(conn: sqlite3.Connection, user_id: int) -> dict[str, float]:
    rows = conn.execute("SELECT board, SUM(points) AS p FROM score_events WHERE user_id=? GROUP BY board", (user_id,)).fetchall()
    t = {r["board"]: round(r["p"] or 0, 2) for r in rows}
    return {"contribution_xp": t.get("contribution", 0.0), "participation_xp": t.get("participation", 0.0),
            "total_xp": round(t.get("contribution", 0.0) + t.get("participation", 0.0), 2)}


def level_for(conn: sqlite3.Connection, xp: float) -> dict:
    lv = config.get(conn, "levels")
    th, titles = lv["thresholds"], lv["titles"]
    k = max(i for i, t in enumerate(th) if xp >= t)
    nxt = th[k + 1] if k + 1 < len(th) else None
    return {"level": k + 1, "title": titles[min(k, len(titles) - 1)], "xp": xp, "level_floor": th[k],
            "next_level_at": nxt, "progress": None if nxt is None else round((xp - th[k]) / (nxt - th[k]), 3)}
