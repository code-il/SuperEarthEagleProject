"""The Daily Survey: a shared puzzle whose real job is to get as much uncharted sky looked at as possible.

How a day works
  * Every player gets the same ``anchors`` for the day: catalogued stars whose class is known.
    They are the shared puzzle. They are scored against the catalogue class, so everyone is
    ranked on the same objects, Wordle-style (exact = green, neighbouring class = yellow).
  * Mixed in, indistinguishable from the anchors, are ``real`` uncharted sources: objects with no
    class in any catalogue. These are picked *per player* to spread coverage: the least-classified
    sources go first, sources already handed to someone else today count as taken, and a source
    stops being handed out once ``votes_wanted`` independent classifications have passed QA.
    When the open pool runs low, more of the catalogue is opened automatically.
  * Anchor results are revealed only when the run is finished, so a player cannot tell which
    targets are scored while working, and puts the same care into every one.
  * After the run, "keep surveying" hands out one real source at a time without limit, with a
    hidden anchor every ``bonus_anchor_every`` targets. Those anchors are not part of the daily
    score; they measure the player's accuracy over time (``reliability``), which weights their
    votes in each candidate's consensus.

Ranking: daily score on the shared anchors, then real coordinates surveyed (passed QA), then
finish time. Rooms are private groups (a class, a club, friends) that see a board of their own.

Every answer goes through the normal submission pipeline (``contributions.submit``): the same
QA, the same points, the same candidates and blind review. Nothing here invents data.

Known limit: the image viewer needs a target's coordinates, so a determined player could look an
anchor up in SIMBAD. Names, magnitudes and distances are hidden to make that a deliberate act.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import date, timedelta
from typing import Any

from . import challenges, config, contributions, investigations, quality
from .db import j, new_id, now, uj

SEQ = ["ob", "a", "f", "g", "k", "m", "bd"]      # the temperature sequence, hot to cool
TILE = {"exact": "🟩", "near": "🟨", "miss": "⬛", "skipped": "⬜"}
PASSED = contributions.PASSED
ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class DailyError(ValueError):
    pass


def _h(*parts: Any) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()


def today() -> str:
    return challenges.today()


def puzzle_number(conn: sqlite3.Connection, day: str) -> int:
    launch = date.fromisoformat(config.get(conn, "daily")["launch_date"])
    return (date.fromisoformat(day) - launch).days + 1


def grade(guess: str | None, answer: str | None) -> str:
    """exact / near (neighbouring spectral class, or a giant vs the G/K/M classes giants mostly are) / miss."""
    if guess is None:
        return "skipped"
    if guess == answer:
        return "exact"
    if guess in SEQ and answer in SEQ and abs(SEQ.index(guess) - SEQ.index(answer)) == 1:
        return "near"
    if {guess, answer} & {"giant"} and {guess, answer} - {"giant"} <= {"g", "k", "m"}:
        return "near"
    return "miss"


# ---------------------------------------------------------------- anchors
def _pick_anchor(conn: sqlite3.Connection, seed: str, avoid_classes: set[str], exclude: set[str], prefer_existing: bool = False) -> str:
    """An anchor: a reserved training investigation on a catalogued star. Either a fresh star from
    the catalogue, or (when ``prefer_existing``, or when the catalogue's supply runs out) a star
    already used as an anchor that is not in ``exclude``."""
    existing = [r["id"] for r in conn.execute("SELECT id FROM investigations WHERE category='classification' AND training=1 AND status='open' "
                                              "AND json_extract(data, '$.daily_reserved') IS NOT NULL") if r["id"] not in exclude]
    existing.sort(key=lambda i: _h(seed, i))
    reuse = next((i for i in existing if _answer_of(conn, i) not in avoid_classes), existing[0] if existing else None)
    if prefer_existing and reuse:
        return reuse
    try:
        return _make_anchor(conn, seed, avoid_classes)
    except DailyError:
        if reuse:
            return reuse
        raise


def _make_anchor(conn: sqlite3.Connection, seed: str, avoid_classes: set[str]) -> str:
    """Create a reserved training investigation on a catalogued star nobody has been given yet."""
    c = investigations.catalog()
    used = {r[0] for r in conn.execute("SELECT source_ref FROM investigations WHERE category='classification'")}
    cands = [int(i) for i in investigations._candidates_for("classification", True) if f"atlas:{int(i)}" not in used]
    if not cands:
        raise DailyError("no catalogued stars left to use as anchors")
    cands.sort(key=lambda i: _h(seed, i))
    pick = next((i for i in cands if investigations.CLASS_KEYS[int(c["cls"][i])] not in avoid_classes), cands[0])
    rec = investigations._record(c, pick)
    iid = new_id("INV")
    conn.execute("INSERT INTO investigations(id, category, source_ref, source_name, ra, dec, difficulty, status, training, instructions, "
                 "data, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (iid, "classification", f"atlas:{pick}", rec["name"], float(c["ra"][pick]), float(c["dec"][pick]), 2, "open", 1,
                  investigations.INSTRUCTIONS["classification"], j({**rec, "training_answer": rec["class_key"], "daily_reserved": True}), now()))
    return iid


def _answer_of(conn: sqlite3.Connection, iid: str) -> str | None:
    row = conn.execute("SELECT data FROM investigations WHERE id=?", (iid,)).fetchone()
    return uj(row["data"], {}).get("training_answer") if row else None


def ensure_anchors(conn: sqlite3.Connection, day: str | None = None) -> list[str]:
    """Today's shared anchors (created on first use, with as many different classes as possible)."""
    day = day or today()
    rows = conn.execute("SELECT investigation_id FROM daily_anchors WHERE day=? ORDER BY slot", (day,)).fetchall()
    if rows:
        return [r[0] for r in rows]
    n = config.get(conn, "daily")["anchors"]
    ids: list[str] = []
    classes: set[str] = set()
    since = (date.fromisoformat(day) - timedelta(days=60)).isoformat()
    recent = {r[0] for r in conn.execute("SELECT investigation_id FROM daily_anchors WHERE day>=?", (since,))}
    for k in range(n):
        iid = _pick_anchor(conn, f"{day}:anchor:{k}", classes, recent | set(ids))
        classes.add(_answer_of(conn, iid) or "")
        conn.execute("INSERT INTO daily_anchors(day, slot, investigation_id) VALUES (?,?,?)", (day, k, iid))
        ids.append(iid)
    return ids


def replace_unavailable_anchors(conn: sqlite3.Connection, day: str | None = None) -> int:
    """An anchor whose position SPHEREx has not observed cannot be played: swap it for another
    star (everyone still gets the same one). Returns how many were replaced."""
    day = day or today()
    replaced = 0
    for r in conn.execute("SELECT a.slot, a.investigation_id FROM daily_anchors a JOIN investigations i ON i.id=a.investigation_id "
                          "WHERE a.day=? AND i.status='data_unavailable'", (day,)).fetchall():
        others = {_answer_of(conn, x[0]) for x in conn.execute("SELECT investigation_id FROM daily_anchors WHERE day=? AND slot!=?", (day, r["slot"]))}
        todays = {x[0] for x in conn.execute("SELECT investigation_id FROM daily_anchors WHERE day=?", (day,))}
        new = _pick_anchor(conn, f"{day}:anchor:{r['slot']}:{r['investigation_id']}", {x for x in others if x}, todays)
        conn.execute("UPDATE daily_anchors SET investigation_id=? WHERE day=? AND slot=?", (new, day, r["slot"]))
        conn.execute("UPDATE daily_items SET investigation_id=? WHERE day=? AND investigation_id=? AND status='open'", (new, day, r["investigation_id"]))
        replaced += 1
    return replaced


# ---------------------------------------------------------------- real sources, coverage first
def _real_candidates(conn: sqlite3.Connection, uid: int, day: str, n: int, exclude: set[str] = frozenset()) -> list[str]:
    cfg = config.get(conn, "daily")
    votes = cfg["votes_wanted"]
    inflight = {r[0]: r[1] for r in conn.execute(
        "SELECT investigation_id, COUNT(*) FROM daily_items WHERE status='open' AND anchor=0 AND day=? GROUP BY investigation_id", (day,))}

    def open_rows():
        return conn.execute("SELECT id, n_submissions FROM investigations WHERE category='classification' AND training=0 AND status='open' "
                            f"AND {investigations.RESERVED}").fetchall()

    rows = open_rows()
    unsat = sum(1 for r in rows if r["n_submissions"] + inflight.get(r["id"], 0) < votes)
    want = n + cfg["pool_headroom"]
    if unsat < want:   # open more of the uncharted sky
        investigations.ensure_pool(conn, "classification", extra_real=len(rows) + want - unsat
                                   - int(round(config.get(conn, "pool")["open_per_category"] * (1 - config.get(conn, "pool")["training_fraction"]))))
        rows = open_rows()
    done = {r[0] for r in conn.execute("SELECT investigation_id FROM contributions WHERE user_id=?", (uid,))}
    done |= {r[0] for r in conn.execute("SELECT investigation_id FROM daily_items WHERE user_id=?", (uid,))}
    rows = [r for r in rows if r["id"] not in done and r["id"] not in exclude]
    rows.sort(key=lambda r: (r["n_submissions"] + inflight.get(r["id"], 0) >= votes,
                             r["n_submissions"] + inflight.get(r["id"], 0), _h(day, uid, r["id"])))
    return [r["id"] for r in rows[:n]]


# ---------------------------------------------------------------- runs
def _run(conn: sqlite3.Connection, uid: int, day: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM daily_runs WHERE day=? AND user_id=?", (day, uid)).fetchone()


def start(conn: sqlite3.Connection, user: sqlite3.Row) -> dict[str, Any]:
    if user["status"] != "active":
        raise DailyError("account suspended")
    day, uid = today(), user["id"]
    if _run(conn, uid, day) is None:
        cfg = config.get(conn, "daily")
        anchors = ensure_anchors(conn, day)
        reals = _real_candidates(conn, uid, day, cfg["real"], set(anchors))
        items = [(iid, 1, k) for k, iid in enumerate(anchors)] + [(iid, 0, None) for iid in reals]
        items.sort(key=lambda t: _h(day, uid, t[0]))      # anchors sit in different places for each player
        conn.execute("INSERT INTO daily_runs(day, user_id, started_at, max_score) VALUES (?,?,?,?)",
                     (day, uid, now(), len(anchors) * cfg["exact_points"]))
        for slot, (iid, anchor, aslot) in enumerate(items):
            conn.execute("INSERT INTO daily_items(day, user_id, slot, investigation_id, anchor, anchor_slot) VALUES (?,?,?,?,?,?)",
                         (day, uid, slot, iid, anchor, aslot))
    return view(conn, uid)


def item_investigation(conn: sqlite3.Connection, uid: int, slot: int, day: str | None = None) -> str:
    row = conn.execute("SELECT investigation_id FROM daily_items WHERE day=? AND user_id=? AND slot=?", (day or today(), uid, slot)).fetchone()
    if row is None:
        raise DailyError("no such target in today's survey")
    return row[0]


def _refresh(conn: sqlite3.Connection, uid: int, day: str) -> None:
    cfg = config.get(conn, "daily")
    pts = {"exact": cfg["exact_points"], "near": cfg["near_points"]}
    items = conn.execute("SELECT * FROM daily_items WHERE day=? AND user_id=?", (day, uid)).fetchall()
    score = sum(pts.get(i["grade"], 0) for i in items if i["anchor"] and not i["bonus"])
    surveyed = sum(1 for i in items if not i["anchor"] and i["status"] == "answered" and i["qc_status"] in PASSED)
    finished = all(i["status"] != "open" for i in items if not i["bonus"])
    conn.execute("UPDATE daily_runs SET score=?, surveyed=?, finished_at=CASE WHEN ? AND finished_at IS NULL THEN ? ELSE finished_at END "
                 "WHERE day=? AND user_id=?", (score, surveyed, int(finished), now(), day, uid))


def answer(conn: sqlite3.Connection, user: sqlite3.Row, slot: int, payload: dict[str, Any]) -> dict[str, Any]:
    day, uid = today(), user["id"]
    it = conn.execute("SELECT * FROM daily_items WHERE day=? AND user_id=? AND slot=?", (day, uid, slot)).fetchone()
    if it is None:
        raise DailyError("no such target in today's survey")
    if it["status"] != "open":
        raise DailyError("you have already answered this target")
    inv = conn.execute("SELECT * FROM investigations WHERE id=?", (it["investigation_id"],)).fetchone()
    if inv["status"] != "open" or inv["obs_refs"] is None:
        raise DailyError("this target's SPHEREx images are not available yet; try again shortly or skip it")
    # Check first without recording, so an incomplete answer can be fixed instead of burning the target.
    pre = quality.run(investigations.out(inv, include_answer=True), payload if isinstance(payload, dict) else {}, duplicate=False,
                      cfg=config.get(conn, "quality"))
    if pre["status"] in ("incomplete", "rejected"):
        return {"slot": slot, "accepted": False, "qc_status": pre["status"], "missing": pre["missing"], "errors": pre["errors"]}
    try:
        res = contributions.submit(conn, user, inv["id"], payload)
    except contributions.SubmitError as e:
        raise DailyError(str(e)) from e
    label = res["qc"].get("label")
    g = grade(label, _answer_of(conn, inv["id"])) if it["anchor"] else None
    conn.execute("UPDATE daily_items SET status='answered', contribution_id=?, qc_status=?, label=?, grade=?, answered_at=? "
                 "WHERE day=? AND user_id=? AND slot=?", (res["contribution_id"], res["qc"]["status"], label, g, now(), day, uid, slot))
    _refresh(conn, uid, day)
    # Deliberately no points, rewards or training feedback here: they would reveal which targets are anchors.
    return {"slot": slot, "accepted": True, "qc_status": res["qc"]["status"], "warnings": res["qc"]["warnings"],
            "achievements": res["achievements"], "finished": _run(conn, uid, day)["finished_at"] is not None}


def skip(conn: sqlite3.Connection, uid: int, slot: int) -> dict[str, Any]:
    day = today()
    it = conn.execute("SELECT * FROM daily_items WHERE day=? AND user_id=? AND slot=?", (day, uid, slot)).fetchone()
    if it is None or it["status"] != "open":
        raise DailyError("nothing to skip")
    conn.execute("UPDATE daily_items SET status='skipped', grade=?, answered_at=? WHERE day=? AND user_id=? AND slot=?",
                 ("skipped" if it["anchor"] else None, now(), day, uid, slot))
    _refresh(conn, uid, day)
    return {"slot": slot, "finished": _run(conn, uid, day)["finished_at"] is not None}


def swap_unavailable(conn: sqlite3.Connection, uid: int, slot: int) -> bool:
    """A real target SPHEREx never observed is replaced by another uncharted source."""
    day = today()
    it = conn.execute("SELECT i.status, d.anchor, d.status AS item_status FROM daily_items d JOIN investigations i ON i.id=d.investigation_id "
                      "WHERE d.day=? AND d.user_id=? AND d.slot=?", (day, uid, slot)).fetchone()
    if it is None or it["anchor"] or it["item_status"] != "open" or it["status"] != "data_unavailable":
        return False
    new = _real_candidates(conn, uid, day, 1)
    if not new:
        return False
    conn.execute("UPDATE daily_items SET investigation_id=? WHERE day=? AND user_id=? AND slot=?", (new[0], day, uid, slot))
    return True


def bonus(conn: sqlite3.Connection, user: sqlite3.Row) -> dict[str, Any]:
    """After the run: one more target at a time, with a hidden anchor every few."""
    day, uid = today(), user["id"]
    run = _run(conn, uid, day)
    if run is None or run["finished_at"] is None:
        raise DailyError("finish today's survey first")
    if conn.execute("SELECT 1 FROM daily_items WHERE day=? AND user_id=? AND bonus=1 AND status='open'", (day, uid)).fetchone() is None:
        cfg = config.get(conn, "daily")
        k = conn.execute("SELECT COUNT(*) FROM daily_items WHERE day=? AND user_id=? AND bonus=1", (day, uid)).fetchone()[0] + 1
        slot = conn.execute("SELECT MAX(slot) FROM daily_items WHERE day=? AND user_id=?", (day, uid)).fetchone()[0] + 1
        if cfg["bonus_anchor_every"] and k % cfg["bonus_anchor_every"] == 0:
            seen = {r[0] for r in conn.execute("SELECT investigation_id FROM daily_items WHERE user_id=?", (uid,))}
            seen |= {r[0] for r in conn.execute("SELECT investigation_id FROM contributions WHERE user_id=?", (uid,))}
            seen |= {r[0] for r in conn.execute("SELECT investigation_id FROM daily_anchors WHERE day=?", (day,))}
            iid, anchor = _pick_anchor(conn, f"{day}:{uid}:bonus:{k}", set(), seen, prefer_existing=True), 1
        else:
            got = _real_candidates(conn, uid, day, 1)
            if not got:
                raise DailyError("every open source has enough classifications right now; come back tomorrow")
            iid, anchor = got[0], 0
        conn.execute("INSERT INTO daily_items(day, user_id, slot, investigation_id, anchor, bonus) VALUES (?,?,?,?,?,1)",
                     (day, uid, slot, iid, anchor))
    return view(conn, uid)


# ---------------------------------------------------------------- reading
def reliability(conn: sqlite3.Connection, uid: int) -> dict[str, Any]:
    """Accuracy on every anchor answered (daily and bonus), with a weak prior so new players start at ~0.5."""
    rows = conn.execute("SELECT grade FROM daily_items WHERE user_id=? AND anchor=1 AND grade IN ('exact','near','miss')", (uid,)).fetchall()
    exact = sum(r[0] == "exact" for r in rows)
    near = sum(r[0] == "near" for r in rows)
    return {"anchors_answered": len(rows), "exact": exact, "near": near,
            "score": round((exact + 0.5 * near + 1) / (len(rows) + 2), 3)}


def reliability_weights(conn: sqlite3.Connection, uids: list[int]) -> dict[int, float]:
    return {u: reliability(conn, u)["score"] for u in set(uids)}


def tiles(conn: sqlite3.Connection, uid: int, day: str) -> str:
    rows = conn.execute("SELECT grade FROM daily_items WHERE day=? AND user_id=? AND anchor=1 AND bonus=0 ORDER BY anchor_slot", (day, uid)).fetchall()
    return "".join(TILE.get(r[0] or "", "⬜") for r in rows)


def streak(conn: sqlite3.Connection, uid: int) -> int:
    days = {r[0] for r in conn.execute("SELECT day FROM daily_runs WHERE user_id=? AND finished_at IS NOT NULL", (uid,))}
    d = date.fromisoformat(today())
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def _rank(conn: sqlite3.Connection, uid: int, day: str) -> tuple[int | None, int]:
    rows = conn.execute("SELECT user_id FROM daily_runs WHERE day=? AND finished_at IS NOT NULL ORDER BY score DESC, surveyed DESC, finished_at",
                        (day,)).fetchall()
    return next((k for k, r in enumerate(rows, 1) if r[0] == uid), None), len(rows)


def community(conn: sqlite3.Connection, day: str) -> dict[str, Any]:
    votes = config.get(conn, "daily")["votes_wanted"]
    return {
        "players_today": conn.execute("SELECT COUNT(*) FROM daily_runs WHERE day=?", (day,)).fetchone()[0],
        "coordinates_today": conn.execute("SELECT COUNT(DISTINCT investigation_id) FROM daily_items WHERE day=? AND anchor=0 AND status='answered' "
                                          "AND qc_status IN ('passed','passed_with_warnings')", (day,)).fetchone()[0],
        "sources_with_enough_votes": conn.execute("SELECT COUNT(*) FROM investigations WHERE category='classification' AND training=0 AND n_submissions>=?",
                                                  (votes,)).fetchone()[0],
        "uncharted_open": conn.execute("SELECT COUNT(*) FROM investigations WHERE category='classification' AND training=0 AND status='open' "
                                       "AND n_submissions<?", (votes,)).fetchone()[0],
        "votes_wanted": votes,
    }


def view(conn: sqlite3.Connection, uid: int | None) -> dict[str, Any]:
    day = today()
    out: dict[str, Any] = {"day": day, "number": puzzle_number(conn, day), "community": community(conn, day), "run": None}
    if uid is None:
        return out
    run = _run(conn, uid, day)
    out["streak"] = streak(conn, uid)
    out["reliability"] = reliability(conn, uid)
    if run is None:
        return out
    finished = run["finished_at"] is not None
    items = []
    for it in conn.execute("SELECT d.*, i.ra, i.dec, i.status AS inv_status, i.obs_refs, i.data FROM daily_items d "
                           "JOIN investigations i ON i.id=d.investigation_id WHERE d.day=? AND d.user_id=? ORDER BY d.slot", (day, uid)):
        refs = uj(it["obs_refs"], None)
        e = {"slot": it["slot"], "label": f"Target {it['slot'] + 1}", "bonus": bool(it["bonus"]), "status": it["status"],
             "ra": it["ra"], "dec": it["dec"], "data_status": it["inv_status"], "observations": refs,
             "observation_count": None if refs is None else len(refs), "my_label": it["label"], "qc_status": it["qc_status"]}
        if finished and not it["bonus"]:      # the reveal
            e["anchor"] = bool(it["anchor"])
            if it["anchor"]:
                e.update(grade=it["grade"], answer=uj(it["data"], {}).get("training_answer"), anchor_slot=it["anchor_slot"])
        elif finished and it["bonus"] and it["status"] != "open":
            e["anchor"] = bool(it["anchor"])
            if it["anchor"]:
                e.update(grade=it["grade"], answer=uj(it["data"], {}).get("training_answer"))
        items.append(e)
    rank, total = _rank(conn, uid, day) if finished else (None, 0)
    r = {"started_at": run["started_at"], "finished_at": run["finished_at"], "surveyed": run["surveyed"], "items": items}
    if finished:
        t = tiles(conn, uid, day)
        pts = conn.execute("SELECT COALESCE(SUM(e.points),0) FROM score_events e JOIN daily_items d ON d.contribution_id=e.ref_id "
                           "WHERE d.day=? AND d.user_id=? AND e.board=?", (day, uid, "contribution")).fetchone()[0]
        xp = conn.execute("SELECT COALESCE(SUM(e.points),0) FROM score_events e JOIN daily_items d ON d.contribution_id=e.ref_id "
                          "WHERE d.day=? AND d.user_id=? AND e.board=?", (day, uid, "participation")).fetchone()[0]
        r.update(score=run["score"], max_score=run["max_score"], tiles=t, rank=rank, ranked=total,
                 contribution_points=round(pts, 2), participation_xp=round(xp, 2),
                 share=f"SPHEREx Daily #{out['number']} {run['score']}/{run['max_score']}\n{t}\n🔭 {run['surveyed']} real sky coordinates surveyed")
    out["run"] = r
    return out


# ---------------------------------------------------------------- leaderboards and rooms
def _member(conn: sqlite3.Connection, code: str, uid: int) -> bool:
    return conn.execute("SELECT 1 FROM room_members WHERE code=? AND user_id=?", (code, uid)).fetchone() is not None


def leaderboard(conn: sqlite3.Connection, day: str | None = None, room: str | None = None, uid: int | None = None,
                order: str = "score", limit: int = 100) -> dict[str, Any]:
    day = day or today()
    try:
        date.fromisoformat(day)
    except ValueError as e:
        raise DailyError("day must be YYYY-MM-DD") from e
    if order not in ("score", "surveyed"):
        raise DailyError("order must be score or surveyed")
    q = ("SELECT r.*, u.username, u.display_name FROM daily_runs r JOIN users u ON u.id=r.user_id "
         "WHERE r.day=? AND r.finished_at IS NOT NULL AND u.status='active'")
    args: list[Any] = [day]
    name = None
    if room:
        room = room.upper()
        rr = conn.execute("SELECT * FROM rooms WHERE code=?", (room,)).fetchone()
        if rr is None:
            raise DailyError("no such room")
        if uid is None or not _member(conn, room, uid):
            raise DailyError("join the room to see its board")
        name = rr["name"]
        q += " AND r.user_id IN (SELECT user_id FROM room_members WHERE code=?)"
        args.append(room)
    q += " ORDER BY r.score DESC, r.surveyed DESC, r.finished_at" if order == "score" else " ORDER BY r.surveyed DESC, r.score DESC, r.finished_at"
    rows = conn.execute(q, args).fetchall()
    entries = [{"rank": k, "user_id": r["user_id"], "name": r["display_name"] or r["username"], "score": r["score"],
                "max_score": r["max_score"], "surveyed": r["surveyed"], "tiles": tiles(conn, r["user_id"], day),
                "finished_at": r["finished_at"], "me": r["user_id"] == uid} for k, r in enumerate(rows, 1)]
    me = next((e for e in entries if e["me"]), None)
    return {"day": day, "number": puzzle_number(conn, day), "room": room, "room_name": name, "order": order,
            "total": len(entries), "entries": entries[:limit], "me": me}


def create_room(conn: sqlite3.Connection, uid: int, name: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not 1 <= len(name) <= 60:
        raise DailyError("room name must be 1–60 characters")
    if conn.execute("SELECT COUNT(*) FROM rooms WHERE created_by=?", (uid,)).fetchone()[0] >= 20:
        raise DailyError("you have created the maximum number of rooms")
    for _ in range(20):
        code = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(6))
        if conn.execute("SELECT 1 FROM rooms WHERE code=?", (code,)).fetchone() is None:
            break
    conn.execute("INSERT INTO rooms(code, name, created_by, created_at) VALUES (?,?,?,?)", (code, name, uid, now()))
    conn.execute("INSERT INTO room_members(code, user_id, joined_at) VALUES (?,?,?)", (code, uid, now()))
    return {"code": code, "name": name}


def join_room(conn: sqlite3.Connection, uid: int, code: str) -> dict[str, Any]:
    code = (code or "").strip().upper()
    r = conn.execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()
    if r is None:
        raise DailyError("no room with that code")
    conn.execute("INSERT OR IGNORE INTO room_members(code, user_id, joined_at) VALUES (?,?,?)", (code, uid, now()))
    return {"code": code, "name": r["name"]}


def leave_room(conn: sqlite3.Connection, uid: int, code: str) -> None:
    conn.execute("DELETE FROM room_members WHERE code=? AND user_id=?", ((code or "").upper(), uid))


def my_rooms(conn: sqlite3.Connection, uid: int) -> list[dict[str, Any]]:
    return [{"code": r["code"], "name": r["name"], "members": r["n"], "owner": r["created_by"] == uid}
            for r in conn.execute("SELECT r.*, (SELECT COUNT(*) FROM room_members m2 WHERE m2.code=r.code) AS n FROM rooms r "
                                  "JOIN room_members m ON m.code=r.code WHERE m.user_id=? ORDER BY m.joined_at", (uid,))]
