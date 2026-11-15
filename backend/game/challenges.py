"""Rotating daily challenges, generated from tasks that actually exist today.

Each UTC day gets up to three challenges, picked deterministically from the templates whose
task pool is not empty (e.g. "review another player's submission" only appears when some
candidate needs a review). A login never completes a challenge: the matching contribution
or review must pass the automated checks. Rewards are participation XP only.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

from . import config, scoring
from .db import j, now, uj

TEMPLATES = [
    ("unclassified", "Classify an uncharted source", "Classify a source that has no class in any catalogue yet.",
     {"type": "contribution", "category": "classification", "unclassified": True}),
    ("compare", "Compare two epochs", "Complete a moving-object investigation: measure a source at two different dates.",
     {"type": "contribution", "category": "moving"}),
    ("review", "Review another investigator", "Independently assess a candidate submitted by someone else.",
     {"type": "review"}),
    ("spectral", "Read an infrared spectrum", "Investigate absorption features in a SPHEREx spectrum.",
     {"type": "contribution", "category": "spectral"}),
    ("artefact", "Check image quality", "Inspect a bright-star field for artefacts.",
     {"type": "contribution", "category": "image_quality"}),
]


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _available(conn: sqlite3.Connection, crit: dict) -> bool:
    if crit["type"] == "review":
        return conn.execute("SELECT 1 FROM candidates WHERE status IN ('community_review','evidence_sufficient') LIMIT 1").fetchone() is not None
    q = "SELECT 1 FROM investigations WHERE status='open' AND training=0 AND category=?"
    if crit.get("unclassified"):
        q += " AND json_extract(data, '$.class_source')='none'"
    return conn.execute(q + " LIMIT 1", (crit["category"],)).fetchone() is not None


def ensure_today(conn: sqlite3.Connection) -> list[dict]:
    day = today()
    rows = conn.execute("SELECT * FROM daily_challenges WHERE day=?", (day,)).fetchall()
    if not rows:
        reward = config.get(conn, "participation")["challenge_default"]
        avail = [t for t in TEMPLATES if _available(conn, t[3])]
        avail.sort(key=lambda t: hashlib.sha256(f"{day}:{t[0]}".encode()).hexdigest())
        for key, title, desc, crit in avail[:3]:
            conn.execute("INSERT OR IGNORE INTO daily_challenges(day, key, title, description, criteria, reward_xp) VALUES (?,?,?,?,?,?)",
                         (day, key, title, desc, j(crit), reward))
        rows = conn.execute("SELECT * FROM daily_challenges WHERE day=?", (day,)).fetchall()
    return [dict(r, criteria=uj(r["criteria"], {})) for r in rows]


def for_user(conn: sqlite3.Connection, uid: int) -> list[dict]:
    out = []
    for c in ensure_today(conn):
        done = conn.execute("SELECT completed_at, ref_id FROM challenge_completions WHERE user_id=? AND day=? AND key=?",
                            (uid, c["day"], c["key"])).fetchone()
        out.append({**c, "completed": done is not None, "completed_at": done["completed_at"] if done else None})
    return out


def on_event(conn: sqlite3.Connection, uid: int, kind: str, category: str | None, ref_id: str, unclassified: bool = False) -> list[dict]:
    """Mark challenges completed by a qualifying contribution/review; returns rewards."""
    done = []
    for c in ensure_today(conn):
        crit = c["criteria"]
        if crit["type"] != kind or (kind == "contribution" and crit.get("category") != category):
            continue
        if crit.get("unclassified") and not unclassified:
            continue
        if conn.execute("INSERT OR IGNORE INTO challenge_completions(user_id, day, key, ref_id, completed_at) VALUES (?,?,?,?,?)",
                        (uid, c["day"], c["key"], ref_id, now())).rowcount:
            ev = scoring.award(conn, uid, "participation", "daily_challenge", c["reward_xp"], f"chal:{uid}:{c['day']}:{c['key']}",
                               ref_type="challenge", ref_id=c["key"], note=c["title"], decay=False)
            if ev:
                done.append({**ev, "challenge": c["title"]})
    return done
