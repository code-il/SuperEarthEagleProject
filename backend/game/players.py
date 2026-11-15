"""Investigator profiles, daily check-in and login streaks.

Calendar days are the player's own: the profile stores an IANA timezone name and the browser's
UTC offset. When the server has timezone data (Linux/Docker, or Python's tzdata package) the
IANA zone is used, which follows daylight-saving changes; otherwise the offset the browser
reports at check-in is used (it is already correct for that instant, DST included).

Rules that keep streaks honest:
  * one qualifying day per local calendar date (database primary key);
  * a date can only extend the streak if it is exactly the day after the last one; a gap
    resets it to 1; a date at or before the last one (e.g. after travelling west) neither
    extends nor breaks it;
  * the timezone used for streaks can change at most once per 24 hours, so a player cannot
    hop timezones to collect two "days" in one;
  * offsets outside UTC-12..UTC+14 are rejected.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import achievements, config, scoring
from .db import audit, j, now, uj

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

MILESTONES = [3, 7, 14, 30, 60, 100]


def _zone(name: str | None):
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001  (no tz database on this server, or unknown name)
        return None


def local_date(at_utc: datetime, tz_name: str | None, offset_min: int | None) -> date:
    z = _zone(tz_name)
    if z is not None:
        return at_utc.astimezone(z).date()
    return (at_utc + timedelta(minutes=offset_min or 0)).date()


def checkin(conn: sqlite3.Connection, user: sqlite3.Row, tz_name: str | None, offset_min: int | None,
            at_utc: datetime | None = None) -> dict[str, Any]:
    """Record today's login (idempotent) and update the streak. Returns what happened."""
    at = at_utc or datetime.now(timezone.utc)
    if offset_min is not None and not (-720 <= offset_min <= 840):
        raise ValueError("timezone offset out of range")
    uid = user["id"]
    stored_name, stored_off = user["tz_name"], user["tz_offset_min"]
    changed_at = user["tz_changed_at"]
    if (tz_name, offset_min) != (stored_name, stored_off) and (tz_name or offset_min is not None):
        recent = changed_at and (at - datetime.fromisoformat(changed_at.replace("Z", "+00:00"))) < timedelta(hours=24)
        if stored_name is None and stored_off is None or not recent:
            conn.execute("UPDATE users SET tz_name=?, tz_offset_min=?, tz_changed_at=? WHERE id=?",
                         (tz_name, offset_min, at.isoformat(timespec="seconds").replace("+00:00", "Z"), uid))
            if stored_name is not None or stored_off is not None:
                audit(conn, uid, "user.timezone", "user", uid, {"from": [stored_name, stored_off], "to": [tz_name, offset_min]})
            stored_name, stored_off = tz_name, offset_min
    d = local_date(at, stored_name, stored_off if stored_off is not None else 0)
    ds = d.isoformat()
    out: dict[str, Any] = {"date": ds, "new_day": False, "rewards": [], "milestones": [], "achievements": []}
    ins = conn.execute("INSERT OR IGNORE INTO login_days(user_id, local_date, utc_at, tz_offset_min) VALUES (?,?,?,?)",
                       (uid, ds, at.isoformat(timespec="seconds").replace("+00:00", "Z"), stored_off or 0))
    st = conn.execute("SELECT * FROM streaks WHERE user_id=?", (uid,)).fetchone()
    if ins.rowcount == 0:
        out["streak"] = dict(st)
        return out
    out["new_day"] = True
    last = date.fromisoformat(st["last_date"]) if st["last_date"] else None
    cur = st["current"]
    if last is None or d - last > timedelta(days=1):
        cur = 1
    elif d - last == timedelta(days=1):
        cur += 1
    # d <= last: a timezone move made "today" an earlier date: keep the streak as is.
    new_last = ds if last is None or d > last else st["last_date"]
    longest = max(st["longest"], cur)
    conn.execute("UPDATE streaks SET current=?, longest=?, last_date=?, total_days=total_days+1 WHERE user_id=?",
                 (cur, longest, new_last, uid))
    part = config.get(conn, "participation")
    ev = scoring.award(conn, uid, "participation", "daily_login", part["daily_login"], f"login:{uid}:{ds}",
                       ref_type="login_day", ref_id=ds, note="Daily login", decay=False)
    if ev:
        out["rewards"].append(ev)
    for m in MILESTONES:
        if cur >= m and conn.execute("INSERT OR IGNORE INTO streak_milestones(user_id, days, reached_at) VALUES (?,?,?)",
                                     (uid, m, now())).rowcount:
            out["milestones"].append(m)
            bonus = part["streak_milestone_bonus"].get(str(m), 0)
            if bonus:
                ev = scoring.award(conn, uid, "participation", "streak_milestone", bonus, f"streak:{uid}:{m}",
                                   ref_type="streak", ref_id=str(m), note=f"{m}-day streak", decay=False)
                if ev:
                    out["rewards"].append(ev)
    out["achievements"] = achievements.evaluate(conn, uid)
    out["streak"] = dict(conn.execute("SELECT * FROM streaks WHERE user_id=?", (uid,)).fetchone())
    return out


def stats(conn: sqlite3.Connection, uid: int) -> dict[str, Any]:
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    passed = "qc_status IN ('passed','passed_with_warnings')"
    return {
        "investigations_completed": q(f"SELECT COUNT(DISTINCT investigation_id) FROM contributions WHERE user_id=? AND {passed}", uid),
        "submissions_total": q("SELECT COUNT(*) FROM contributions WHERE user_id=?", uid),
        "valid_submissions": q(f"SELECT COUNT(*) FROM contributions WHERE user_id=? AND {passed} AND training=0", uid),
        "training_completed": q(f"SELECT COUNT(*) FROM contributions WHERE user_id=? AND {passed} AND training=1", uid),
        "reviews_given": q("SELECT COUNT(*) FROM reviews WHERE reviewer_id=?", uid),
        "contributions_independently_reviewed": q(
            f"SELECT COUNT(*) FROM contributions c WHERE c.user_id=? AND {passed} AND EXISTS (SELECT 1 FROM reviews r WHERE r.candidate_id=c.candidate_id)", uid),
        "scientifically_recognised": q(
            "SELECT COUNT(DISTINCT c.id) FROM contributions c JOIN scientific_reviews s ON s.candidate_id=c.candidate_id "
            "WHERE c.user_id=? AND s.simulated=0 AND s.outcome!='not_validated' AND (s.contribution_id IS NULL OR s.contribution_id=c.id) "
            f"AND c.{passed}", uid),
    }


def profile(conn: sqlite3.Connection, uid: int, private: bool = True) -> dict[str, Any]:
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    t = scoring.totals(conn, uid)
    st = conn.execute("SELECT * FROM streaks WHERE user_id=?", (uid,)).fetchone()
    ach = conn.execute("SELECT a.key, a.name, a.description, a.icon, a.rarity, a.kind, p.unlocked_at FROM player_achievements p "
                       "JOIN achievements a ON a.key=p.key WHERE p.user_id=? ORDER BY p.unlocked_at", (uid,)).fetchall()
    specs = conn.execute("SELECT category, COUNT(*) n FROM contributions WHERE user_id=? AND qc_status IN ('passed','passed_with_warnings') "
                         "AND training=0 GROUP BY category ORDER BY n DESC", (uid,)).fetchall()
    out = {
        "id": u["id"], "username": u["username"], "display_name": u["display_name"], "bio": u["bio"],
        "role": u["role"], "created_at": u["created_at"], **t, "level": scoring.level_for(conn, t["total_xp"]),
        "stats": stats(conn, uid),
        "streak": {"current": st["current"], "longest": st["longest"], "last_date": st["last_date"], "total_active_days": st["total_days"],
                   "milestones": [r["days"] for r in conn.execute("SELECT days FROM streak_milestones WHERE user_id=? ORDER BY days", (uid,))]},
        "achievements": [dict(r) for r in ach],
        "specialisations": uj(u["specialisations"], []),
        "derived_specialisations": [{"category": r["category"], "count": r["n"]} for r in specs],
        "ranks": {p: scoring.rank_of(conn, uid, p) for p in ("all", "week", "month")},
    }
    if private:
        out.update({"tz_name": u["tz_name"], "tz_offset_min": u["tz_offset_min"], "share_attribution": bool(u["share_attribution"]),
                    "pseudonym": u["pseudonym"]})
    return out


def update_profile(conn: sqlite3.Connection, uid: int, body: dict[str, Any]) -> None:
    allowed = {"display_name": 60, "bio": 500}
    for k, n in allowed.items():
        if k in body and body[k] is not None:
            conn.execute(f"UPDATE users SET {k}=? WHERE id=?", (str(body[k])[:n], uid))
    if "specialisations" in body:
        valid = {"moving", "classification", "image_quality", "spectral", "verification"}
        specs = [s for s in body["specialisations"] or [] if s in valid][:5]
        conn.execute("UPDATE users SET specialisations=? WHERE id=?", (j(specs), uid))
    if "share_attribution" in body:
        conn.execute("UPDATE users SET share_attribution=? WHERE id=?", (1 if body["share_attribution"] else 0, uid))
    audit(conn, uid, "user.profile", "user", uid, {k: body[k] for k in body if k in (*allowed, "specialisations", "share_attribution")})
