"""Demo players, so the leaderboards look alive in a demonstration.

On by default so the citizen-science pages are never empty in a demonstration; turn it off with
``SPHEREX_GAME_DEMO=0``. Every demo account's username starts with ``demo_`` and the
boards mark those rows as *demo*. Remove them all with ``python -m scripts.seed_demo --clear``.

What is seeded, and what is not:
  * score-ledger events over the last 30 days (so all-time, monthly and weekly boards fill),
    streaks, and finished Daily Survey runs for the last 7 days (scores, 🟩🟨⬛ tiles, sky surveyed);
  * no contributions, candidates or reviews: the science pipeline, the community totals on the
    progress page, consensus labels and evidence exports contain real player work only.
The data is deterministic per player and day, so re-seeding fills new days without changing old ones.
"""
from __future__ import annotations

import random
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import auth, config, daily
from .db import audit

PREFIX = "demo_"
# username suffix, display name, skill (accuracy on daily anchors), activity (share of days played)
PLAYERS = [
    ("nova", "Nova Okafor", 0.85, 0.95), ("priya", "Priya Raman", 0.8, 0.9), ("kenji", "Kenji Watanabe", 0.75, 0.85),
    ("amara", "Amara Diallo", 0.7, 0.8), ("lucas", "Lucas Ferreira", 0.65, 0.85), ("sofia", "Sofía Herrera", 0.72, 0.7),
    ("arjun", "Arjun Mehta", 0.6, 0.9), ("mei", "Mei Lin", 0.78, 0.6), ("tariq", "Tariq Haddad", 0.55, 0.75),
    ("elena", "Elena Petrova", 0.68, 0.65), ("kwame", "Kwame Mensah", 0.5, 0.7), ("ingrid", "Ingrid Larsen", 0.62, 0.55),
    ("rahul", "Rahul Nair", 0.58, 0.6), ("zara", "Zara Ahmed", 0.66, 0.5), ("mateo", "Mateo Rossi", 0.45, 0.6),
    ("hana", "Hana Kim", 0.7, 0.45), ("oliver", "Oliver Bennett", 0.4, 0.55), ("ayesha", "Ayesha Siddiqui", 0.6, 0.4),
    ("diego", "Diego Alvarez", 0.35, 0.5), ("freya", "Freya Nilsson", 0.55, 0.35), ("yusuf", "Yusuf Demir", 0.5, 0.3),
    ("leila", "Leila Moradi", 0.65, 0.3), ("sam", "Sam Carter", 0.3, 0.4), ("ines", "Inês Costa", 0.45, 0.25),
]
CATS = ["classification", "classification", "spectral", "moving", "image_quality", "verification"]
SEQ_GUESS = ["ob", "a", "f", "g", "k", "m", "bd", "wd", "giant", "yso"]


def is_demo(username: str | None) -> bool:
    return bool(username) and username.lower().startswith(PREFIX)


def enabled() -> bool:
    import os
    return os.environ.get("SPHEREX_GAME_DEMO", "1").strip().lower() not in ("0", "false", "no", "off")


def _uid(conn: sqlite3.Connection, suffix: str, name: str) -> int:
    row = conn.execute("SELECT id FROM users WHERE username=?", (PREFIX + suffix,)).fetchone()
    if row:
        return row[0]
    return auth.register(conn, PREFIX + suffix, secrets.token_urlsafe(24), name)   # random password: nobody can sign in


def _at(day: date, rng: random.Random) -> str:
    t = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(seconds=rng.randint(6 * 3600, 22 * 3600))
    return min(t, datetime.now(timezone.utc)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ev(conn, uid, board, kind, pts, cat, key, at):
    conn.execute("INSERT OR IGNORE INTO score_events(user_id, board, kind, points, category, ref_type, ref_id, idem_key, note, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)", (uid, board, kind, pts, cat, "demo", None, key, "demo data", at))


def _near_miss(answer: str, rng: random.Random) -> str:
    seq = daily.SEQ
    if answer in seq and rng.random() < 0.6:
        k = seq.index(answer) + rng.choice([-1, 1])
        if 0 <= k < len(seq):
            return seq[k]
    return rng.choice([c for c in SEQ_GUESS if c != answer])


def seed(conn: sqlite3.Connection, days: int = 30, daily_days: int = 7) -> dict[str, int]:
    """Create/extend the demo players. Idempotent: existing days are left as they are."""
    today = date.fromisoformat(daily.today())
    sc = config.get(conn, "scoring")
    dcfg = config.get(conn, "daily")
    n_events = n_runs = 0
    for suffix, name, skill, activity in PLAYERS:
        uid = _uid(conn, suffix, name)
        streak = longest = run = 0
        for back in range(days - 1, -1, -1):
            d = today - timedelta(days=back)
            rng = random.Random(f"{suffix}:{d.isoformat()}")
            if rng.random() > activity:
                run = 0
                continue
            run += 1
            longest = max(longest, run)
            _ev(conn, uid, "participation", "daily_login", config.get(conn, "participation")["daily_login"], None,
                f"demo:{uid}:{d}:login", _at(d, rng))
            for k in range(rng.randint(1, 3 + int(skill * 5))):
                cat = rng.choice(CATS)
                at = _at(d, rng)
                if cat == "verification":
                    _ev(conn, uid, "contribution", "independent_review", sc["independent_review"], cat, f"demo:{uid}:{d}:{k}:rev", at)
                    n_events += 1
                    continue
                _ev(conn, uid, "contribution", "valid_investigation", sc["valid_investigation"], cat, f"demo:{uid}:{d}:{k}:inv", at)
                if cat in ("classification", "spectral"):
                    _ev(conn, uid, "contribution", "valid_classification", sc["valid_classification"], cat, f"demo:{uid}:{d}:{k}:cls", at)
                if rng.random() < skill * 0.4:
                    _ev(conn, uid, "contribution", "high_quality_evidence", sc["high_quality_evidence"], cat, f"demo:{uid}:{d}:{k}:hq", at)
                n_events += 1
        streak = run
        conn.execute("INSERT INTO streaks(user_id, current, longest, last_date, total_days) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(user_id) DO UPDATE SET current=excluded.current, longest=MAX(longest, excluded.longest), "
                     "last_date=excluded.last_date, total_days=excluded.total_days",
                     (uid, streak, longest, today.isoformat() if streak else None,
                      sum(1 for b in range(days) if random.Random(f"{suffix}:{(today - timedelta(days=b)).isoformat()}").random() <= activity)))

        # Daily Survey runs, scored on the real anchors of each day.
        for back in range(daily_days - 1, -1, -1):
            d = today - timedelta(days=back)
            rng = random.Random(f"{suffix}:{d.isoformat()}:daily")
            if rng.random() > activity or conn.execute("SELECT 1 FROM daily_runs WHERE day=? AND user_id=?", (d.isoformat(), uid)).fetchone():
                continue
            anchors = daily.ensure_anchors(conn, d.isoformat())
            score = 0
            started = _at(d, rng)
            for k, iid in enumerate(anchors):
                answer = daily._answer_of(conn, iid) or "k"
                guess = answer if rng.random() < skill else _near_miss(answer, rng)
                g = daily.grade(guess, answer)
                score += {"exact": dcfg["exact_points"], "near": dcfg["near_points"]}.get(g, 0)
                conn.execute("INSERT OR IGNORE INTO daily_items(day, user_id, slot, investigation_id, anchor, anchor_slot, status, label, grade, "
                             "qc_status, answered_at) VALUES (?,?,?,?,1,?,?,?,?,?,?)",
                             (d.isoformat(), uid, k, iid, k, "answered", guess, g, "passed", started))
            surveyed = rng.randint(max(1, dcfg["real"] - 2), dcfg["real"]) + (rng.randint(0, 12) if rng.random() < activity * 0.6 else 0)
            finished = (datetime.fromisoformat(started.replace("Z", "+00:00")) + timedelta(minutes=rng.randint(6, 40)))
            finished = min(finished, datetime.now(timezone.utc)).isoformat(timespec="seconds").replace("+00:00", "Z")
            conn.execute("INSERT INTO daily_runs(day, user_id, started_at, finished_at, score, max_score, surveyed) VALUES (?,?,?,?,?,?,?)",
                         (d.isoformat(), uid, started, finished, score, len(anchors) * dcfg["exact_points"], surveyed))
            n_runs += 1
    audit(conn, None, "demo.seed", "demo", daily.today(), {"events": n_events, "runs": n_runs})
    return {"players": len(PLAYERS), "events": n_events, "daily_runs": n_runs}


def ensure_today(conn: sqlite3.Connection) -> None:
    """With demo mode on, top up once per UTC day (so the daily and weekly boards stay populated)."""
    if not enabled():
        return
    if conn.execute("SELECT 1 FROM audit_log WHERE action='demo.seed' AND target_id=? LIMIT 1", (daily.today(),)).fetchone():
        return
    seed(conn)


def clear(conn: sqlite3.Connection) -> int:
    ids = [r[0] for r in conn.execute("SELECT id FROM users WHERE username LIKE ? ESCAPE '\\'", (PREFIX.replace("_", "\\_") + "%",))]
    for uid in ids:
        for t in ("score_events", "streaks", "streak_milestones", "login_days", "daily_items", "daily_runs", "room_members",
                  "sessions", "player_achievements", "challenge_completions", "leaderboard_snapshots"):
            conn.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
    audit(conn, None, "demo.clear", "demo", None, {"players": len(ids)})
    return len(ids)

