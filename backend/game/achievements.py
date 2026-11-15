"""Event-driven achievements. ``evaluate`` runs after every scoring event and unlocks whatever
the player now qualifies for (idempotent). Scientific-recognition achievements count only
non-simulated external validation records, never player claims or community agreement."""
from __future__ import annotations

import sqlite3

from . import scoring
from .db import j, now, uj

PASSED = "qc_status IN ('passed','passed_with_warnings')"

# requirement: {"metric": <name>, "at_least": n}
DEFINITIONS = [
    ("first_light", "First Light", "Complete your first investigation.", "spark", "common", "participation", {"metric": "investigations", "at_least": 1}, 10),
    ("classifier", "Classifier", "Submit 10 valid source classifications.", "tag", "common", "participation", {"metric": "classifications", "at_least": 10}, 25),
    ("astrometrist", "Astrometrist", "Complete 25 moving-object investigations.", "crosshair", "rare", "participation", {"metric": "moving", "at_least": 25}, 50),
    ("orbital_detective", "Orbital Detective", "Measure 10 moving objects whose motion matches their known (Gaia) proper motion.", "orbit", "rare", "participation", {"metric": "known_movers", "at_least": 10}, 60),
    ("spectral_explorer", "Spectral Explorer", "Complete 20 spectral investigations.", "spectrum", "rare", "participation", {"metric": "spectral", "at_least": 20}, 50),
    ("artefact_hunter", "Artefact Hunter", "Complete 10 image-quality investigations.", "scan", "common", "participation", {"metric": "image_quality", "at_least": 10}, 25),
    ("validator", "The Validator", "Complete 50 useful independent reviews.", "check", "epic", "participation", {"metric": "reviews", "at_least": 50}, 100),
    ("first_explorer", "First Explorer", "Log in 3 days in a row.", "flame", "common", "participation", {"metric": "streak", "at_least": 3}, 0),
    ("weekly_investigator", "Weekly Investigator", "Log in 7 days in a row.", "flame", "common", "participation", {"metric": "streak", "at_least": 7}, 0),
    ("dedicated_observer", "Dedicated Observer", "Log in 14 days in a row.", "flame", "rare", "participation", {"metric": "streak", "at_least": 14}, 0),
    ("cosmic_investigator", "Cosmic Investigator", "Log in 30 days in a row.", "flame", "rare", "participation", {"metric": "streak", "at_least": 30}, 0),
    ("persistent_observer", "Persistent Observer", "Maintain a 30-day streak.", "calendar", "rare", "participation", {"metric": "longest_streak", "at_least": 30}, 50),
    ("veteran_explorer", "Veteran Explorer", "Log in 60 days in a row.", "flame", "epic", "participation", {"metric": "streak", "at_least": 60}, 0),
    ("elite_citizen_scientist", "Elite Citizen Scientist", "Log in 100 days in a row.", "flame", "legendary", "participation", {"metric": "streak", "at_least": 100}, 0),
    ("cosmic_pioneer", "Cosmic Pioneer", "Contribute to a candidate recognised by an external scientific review.", "star", "legendary", "scientific", {"metric": "recognised", "at_least": 1}, 100),
]


def seed(conn: sqlite3.Connection) -> None:
    for key, name, desc, icon, rarity, kind, req, xp in DEFINITIONS:
        conn.execute("INSERT OR IGNORE INTO achievements(key, name, description, icon, rarity, kind, requirement, reward_xp) "
                     "VALUES (?,?,?,?,?,?,?,?)", (key, name, desc, icon, rarity, kind, j(req), xp))


def _metric(conn: sqlite3.Connection, uid: int, m: str) -> int:
    one = lambda sql: conn.execute(sql, (uid,)).fetchone()[0] or 0  # noqa: E731
    real = f"user_id=? AND {PASSED} AND training=0"
    return {
        "investigations": lambda: one(f"SELECT COUNT(*) FROM contributions WHERE user_id=? AND {PASSED}"),
        "classifications": lambda: one(f"SELECT COUNT(*) FROM contributions WHERE {real} AND category='classification'"),
        "moving": lambda: one(f"SELECT COUNT(*) FROM contributions WHERE {real} AND category='moving'"),
        "spectral": lambda: one(f"SELECT COUNT(*) FROM contributions WHERE {real} AND category='spectral'"),
        "image_quality": lambda: one(f"SELECT COUNT(*) FROM contributions WHERE {real} AND category='image_quality'"),
        "known_movers": lambda: one(f"SELECT COUNT(*) FROM contributions WHERE {real} AND category='moving' "
                                    "AND json_extract(qc, '$.known_motion_consistent') = 1"),
        "reviews": lambda: one("SELECT COUNT(*) FROM reviews WHERE reviewer_id=?"),
        "streak": lambda: one("SELECT current FROM streaks WHERE user_id=?"),
        "longest_streak": lambda: one("SELECT longest FROM streaks WHERE user_id=?"),
        "recognised": lambda: one(
            "SELECT COUNT(*) FROM contributions c JOIN scientific_reviews s ON s.candidate_id=c.candidate_id "
            f"WHERE c.user_id=? AND c.{PASSED} AND s.simulated=0 AND s.outcome!='not_validated' "
            "AND (s.contribution_id IS NULL OR s.contribution_id=c.id)"),
    }[m]()


def evaluate(conn: sqlite3.Connection, uid: int) -> list[dict]:
    got = {r["key"] for r in conn.execute("SELECT key FROM player_achievements WHERE user_id=?", (uid,))}
    unlocked = []
    for a in conn.execute("SELECT * FROM achievements WHERE enabled=1").fetchall():
        if a["key"] in got:
            continue
        req = uj(a["requirement"], {})
        if _metric(conn, uid, req["metric"]) >= req["at_least"]:
            conn.execute("INSERT OR IGNORE INTO player_achievements(user_id, key, unlocked_at) VALUES (?,?,?)", (uid, a["key"], now()))
            if a["reward_xp"]:
                scoring.award(conn, uid, "participation", "achievement", a["reward_xp"], f"ach:{uid}:{a['key']}",
                              ref_type="achievement", ref_id=a["key"], note=a["name"], decay=False)
            unlocked.append({"key": a["key"], "name": a["name"], "description": a["description"], "icon": a["icon"],
                             "rarity": a["rarity"], "kind": a["kind"], "reward_xp": a["reward_xp"]})
    return unlocked
