"""Citizen-science game API (``/api/game``).

All scoring, QA and status changes happen here on the server; the client only sends what the
player observed. Authentication is a bearer token (``Authorization: Bearer ...``). Roles:
player < reviewer < admin. Admin routes check the role on every call.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from backend.game import (admin, auth, challenges, contributions, daily, demo, integration, investigations, leaderboards, players,
                          reviews, scoring)
from backend.game import config as gconfig
from backend.game.db import read, tx, uj

router = APIRouter(prefix="/api/game", tags=["game"])

_rl: dict[str, deque] = defaultdict(deque)
_rl_lock = threading.Lock()


def _limit(key: str, n: int, per_s: float) -> None:
    t = time.monotonic()
    with _rl_lock:
        q = _rl[key]
        while q and t - q[0] > per_s:
            q.popleft()
        if len(q) >= n:
            raise HTTPException(429, "too many attempts; wait a minute")
        q.append(t)


def _token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def optional_user(authorization: str | None = Header(default=None)):
    with read() as conn:
        return auth.user_for_token(conn, _token(authorization))


def current_user(authorization: str | None = Header(default=None)):
    u = optional_user(authorization)
    if u is None:
        raise HTTPException(401, "sign in required")
    return u


def require_admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "administrators only")
    return user


def require_reviewer(user=Depends(current_user)):
    if user["role"] not in ("reviewer", "admin"):
        raise HTTPException(403, "reviewers only")
    return user


def _bad(e: Exception):
    raise HTTPException(400, str(e)) from e


# ---------------------------------------------------------------- auth / me
@router.post("/auth/register")
def register(body: dict[str, Any], request: Request):
    _limit(f"reg:{request.client.host if request.client else '-'}", 5, 3600)
    try:
        with tx() as conn:
            auth.register(conn, str(body.get("username", "")), str(body.get("password", "")), body.get("display_name"))
            token = auth.login(conn, str(body["username"]), str(body["password"]))
    except auth.AuthError as e:
        _bad(e)
    return {"token": token}


@router.post("/auth/login")
def login(body: dict[str, Any], request: Request):
    _limit(f"login:{request.client.host if request.client else '-'}:{str(body.get('username','')).lower()}", 10, 300)
    try:
        with tx() as conn:
            token = auth.login(conn, str(body.get("username", "")), str(body.get("password", "")))
    except auth.AuthError as e:
        raise HTTPException(401, str(e)) from e
    return {"token": token}


@router.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    t = _token(authorization)
    if t:
        with tx() as conn:
            auth.logout(conn, t)
    return {"ok": True}


@router.get("/me")
def me(user=Depends(current_user)):
    with read() as conn:
        return players.profile(conn, user["id"], private=True)


@router.patch("/me")
def update_me(body: dict[str, Any], user=Depends(current_user)):
    with tx() as conn:
        players.update_profile(conn, user["id"], body)
        return players.profile(conn, user["id"], private=True)


@router.post("/checkin")
def checkin(body: dict[str, Any], user=Depends(current_user)):
    off = body.get("offset_min")
    try:
        with tx() as conn:
            return players.checkin(conn, user, body.get("tz_name"), None if off is None else int(off))
    except ValueError as e:
        _bad(e)


@router.get("/players/{uid}")
def player(uid: int):
    with read() as conn:
        u = conn.execute("SELECT status FROM users WHERE id=?", (uid,)).fetchone()
        if u is None or u["status"] != "active":
            raise HTTPException(404, "no such player")
        return players.profile(conn, uid, private=False)


# ---------------------------------------------------------------- investigations
def _ensure_obs(iid: str, force: bool = False) -> None:
    """Attach the archive's observation list; the archive query runs outside any write lock."""
    with read() as conn:
        inv = conn.execute("SELECT * FROM investigations WHERE id=?", (iid,)).fetchone()
    if inv is None or not investigations.needs_observations(inv, force):
        return
    refs = investigations.fetch_observations(inv)
    with tx() as conn:
        investigations.store_observations(conn, iid, refs)


@router.get("/investigations")
def list_investigations(category: str | None = None, user=Depends(optional_user)):
    if category is not None and category not in investigations.CATEGORIES:
        raise HTTPException(400, "unknown category")
    with tx() as conn:
        return {"investigations": investigations.discover(conn, user["id"] if user else None, category)}


@router.get("/investigations/{iid}")
def get_investigation(iid: str, refresh: bool = False, user=Depends(optional_user)):
    _ensure_obs(iid, refresh)
    with read() as conn:
        inv = conn.execute("SELECT * FROM investigations WHERE id=?", (iid,)).fetchone()
        if inv is None:
            raise HTTPException(404, "no such investigation")
        out = investigations.out(inv)
        if user:
            out["my_submissions"] = [dict(r) for r in conn.execute(
                "SELECT id, qc_status, submitted_at FROM contributions WHERE user_id=? AND investigation_id=? ORDER BY submitted_at",
                (user["id"], iid))]
        return out


@router.post("/investigations/{iid}/submit")
def submit(iid: str, body: dict[str, Any], user=Depends(current_user)):
    _limit(f"submit:{user['id']}", 30, 60)
    _ensure_obs(iid)
    try:
        with tx() as conn:
            return contributions.submit(conn, user, iid, body)
    except contributions.SubmitError as e:
        _bad(e)


@router.get("/contributions/mine")
def my_contributions(user=Depends(current_user)):
    with read() as conn:
        return {"contributions": contributions.mine(conn, user["id"])}


@router.get("/contributions/{cid}")
def contribution(cid: str, user=Depends(current_user)):
    with read() as conn:
        c = contributions.get(conn, cid)
    if c is None or (c["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(404, "no such contribution")
    return c


# ---------------------------------------------------------------- review
@router.get("/review/queue")
def review_queue(user=Depends(current_user)):
    with tx() as conn:
        return {"tasks": investigations.discover(conn, user["id"], "verification")}


@router.get("/review/{cand_id}")
def review_open(cand_id: str, user=Depends(current_user)):
    try:
        with tx() as conn:
            return reviews.blind_view(conn, cand_id, user["id"])
    except reviews.ReviewError as e:
        _bad(e)


@router.post("/review/{cand_id}")
def review_submit(cand_id: str, body: dict[str, Any], user=Depends(current_user)):
    try:
        with tx() as conn:
            return reviews.submit(conn, user, cand_id, body)
    except reviews.ReviewError as e:
        _bad(e)


# ---------------------------------------------------------------- boards, progress
@router.get("/leaderboard")
def leaderboard(period: str = "all", category: str | None = None, user=Depends(optional_user)):
    if period not in ("all", "week", "month"):
        raise HTTPException(400, "period must be all, week or month")
    try:
        with tx() as conn:
            demo.ensure_today(conn)
            return leaderboards.contribution(conn, period, category, user["id"] if user else None)
    except ValueError as e:
        _bad(e)


@router.get("/leaderboard/validated")
def validated(simulated: bool = False):
    with read() as conn:
        return leaderboards.validated(conn, simulated)


@router.get("/progress")
def progress():
    with read() as conn:
        return leaderboards.progress(conn)


@router.get("/challenges")
def challenges_today(user=Depends(optional_user)):   # not "daily": that name is the Daily Survey module
    with tx() as conn:
        return {"challenges": challenges.for_user(conn, user["id"]) if user else challenges.ensure_today(conn)}


# ---------------------------------------------------------------- daily survey
def _daily(fn, *a):
    try:
        with tx() as conn:
            return fn(conn, *a)
    except daily.DailyError as e:
        _bad(e)


@router.get("/daily")
def daily_view(user=Depends(optional_user)):
    with read() as conn:
        return daily.view(conn, user["id"] if user else None)


@router.post("/daily/start")
def daily_start(user=Depends(current_user)):
    return _daily(daily.start, user)


@router.get("/daily/items/{slot}")
def daily_item(slot: int, refresh: bool = False, user=Depends(current_user)):
    """One target, with its SPHEREx observation list fetched (archive query outside the write lock).
    A target SPHEREx never observed is swapped: anchors for everyone, real sources for this player."""
    for _ in range(4):
        with read() as conn:
            try:
                iid = daily.item_investigation(conn, user["id"], slot)
            except daily.DailyError as e:
                raise HTTPException(404, str(e)) from e
        _ensure_obs(iid, refresh)
        with tx() as conn:
            st = conn.execute("SELECT status FROM investigations WHERE id=?", (iid,)).fetchone()["status"]
            if st != "data_unavailable":
                break
            if not (daily.replace_unavailable_anchors(conn) or daily.swap_unavailable(conn, user["id"], slot)):
                break
        refresh = False
    with read() as conn:
        v = daily.view(conn, user["id"])
    item = next((i for i in (v["run"] or {}).get("items", []) if i["slot"] == slot), None)
    if item is None:
        raise HTTPException(404, "no such target in today's survey")
    return item


@router.post("/daily/items/{slot}/answer")
def daily_answer(slot: int, body: dict[str, Any], user=Depends(current_user)):
    _limit(f"submit:{user['id']}", 30, 60)
    with read() as conn:
        try:
            _ensure_obs(daily.item_investigation(conn, user["id"], slot))
        except daily.DailyError as e:
            _bad(e)
    return _daily(daily.answer, user, slot, body)


@router.post("/daily/items/{slot}/skip")
def daily_skip(slot: int, user=Depends(current_user)):
    return _daily(daily.skip, user["id"], slot)


@router.post("/daily/bonus")
def daily_bonus(user=Depends(current_user)):
    return _daily(daily.bonus, user)


@router.get("/daily/leaderboard")
def daily_board(room: str | None = None, order: str = "score", day: str | None = None, user=Depends(optional_user)):
    with tx() as conn:
        demo.ensure_today(conn)
    return _daily(daily.leaderboard, day, room, user["id"] if user else None, order)


@router.get("/daily/rooms")
def daily_rooms(user=Depends(current_user)):
    with read() as conn:
        return {"rooms": daily.my_rooms(conn, user["id"])}


@router.post("/daily/rooms")
def daily_create_room(body: dict[str, Any], user=Depends(current_user)):
    _limit(f"room:{user['id']}", 10, 3600)
    return _daily(daily.create_room, user["id"], str(body.get("name") or ""))


@router.post("/daily/rooms/join")
def daily_join_room(body: dict[str, Any], user=Depends(current_user)):
    _limit(f"join:{user['id']}", 20, 600)
    return _daily(daily.join_room, user["id"], str(body.get("code") or ""))


@router.post("/daily/rooms/{code}/leave")
def daily_leave_room(code: str, user=Depends(current_user)):
    _daily(daily.leave_room, user["id"], code)
    return {"ok": True}


@router.get("/achievements")
def all_achievements(user=Depends(optional_user)):
    with read() as conn:
        got = {r["key"]: r["unlocked_at"] for r in conn.execute("SELECT key, unlocked_at FROM player_achievements WHERE user_id=?",
                                                                (user["id"] if user else -1,))}
        return {"achievements": [{**dict(a), "requirement": uj(a["requirement"], {}), "unlocked_at": got.get(a["key"])}
                                 for a in conn.execute("SELECT * FROM achievements WHERE enabled=1 ORDER BY kind, reward_xp")]}


@router.get("/scientific")
def scientific():
    """Candidates and their pipeline status, for the Scientific Contributions page."""
    with read() as conn:
        rows = conn.execute("SELECT c.id FROM candidates c ORDER BY CASE c.status WHEN 'scientifically_reviewed' THEN 0 "
                            "WHEN 'awaiting_scientific_review' THEN 1 WHEN 'evidence_sufficient' THEN 2 WHEN 'community_review' THEN 3 ELSE 4 END, "
                            "c.updated_at DESC LIMIT 200").fetchall()
        return {"candidates": [reviews.candidate_public(conn, r["id"]) for r in rows]}


@router.get("/rules")
def rules():
    """The scoring rules in force, published for transparency."""
    with read() as conn:
        return {k: gconfig.get(conn, k) for k in gconfig.DEFAULTS}


# ---------------------------------------------------------------- integration (inbound)
@router.post("/integrations/inbound-review")
async def inbound_review(request: Request, x_signature: str | None = Header(default=None)):
    raw = await request.body()
    try:
        with tx() as conn:
            return integration.receive_review(conn, raw, x_signature)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except json.JSONDecodeError as e:
        _bad(e)


# ---------------------------------------------------------------- admin
@router.get("/admin/metrics")
def a_metrics(user=Depends(require_admin)):
    with read() as conn:
        return admin.metrics(conn)


@router.get("/admin/users")
def a_users(q: str | None = None, user=Depends(require_admin)):
    with read() as conn:
        return {"users": admin.users(conn, q)}


@router.patch("/admin/users/{uid}")
def a_set_user(uid: int, body: dict[str, Any], user=Depends(require_admin)):
    try:
        with tx() as conn:
            admin.set_user(conn, user["id"], uid, body.get("role"), body.get("status"), body.get("org_id"))
    except ValueError as e:
        _bad(e)
    return {"ok": True}


@router.get("/admin/candidates")
def a_candidates(status: str | None = None, user=Depends(require_reviewer)):
    with read() as conn:
        return {"candidates": admin.candidates(conn, status)}


@router.get("/admin/contributions")
def a_contributions(user=Depends(require_admin)):
    with read() as conn:
        rows = conn.execute("SELECT c.id, c.user_id, u.username, c.category, c.qc_status, c.hidden, c.submitted_at, c.candidate_id "
                            "FROM contributions c JOIN users u ON u.id=c.user_id ORDER BY c.submitted_at DESC LIMIT 300").fetchall()
        return {"contributions": [dict(r) for r in rows]}


@router.post("/admin/contributions/{cid}/moderate")
def a_moderate(cid: str, body: dict[str, Any], user=Depends(require_admin)):
    try:
        with tx() as conn:
            admin.moderate_contribution(conn, user["id"], cid, str(body.get("action")), str(body.get("reason") or ""))
    except ValueError as e:
        _bad(e)
    return {"ok": True}


@router.patch("/admin/investigations/{iid}")
def a_investigation(iid: str, body: dict[str, Any], user=Depends(require_admin)):
    try:
        with tx() as conn:
            admin.set_investigation(conn, user["id"], iid, str(body.get("status")))
    except ValueError as e:
        _bad(e)
    return {"ok": True}


@router.patch("/admin/achievements/{key}")
def a_achievement(key: str, body: dict[str, Any], user=Depends(require_admin)):
    with tx() as conn:
        admin.set_achievement(conn, user["id"], key, bool(body.get("enabled")))
    return {"ok": True}


@router.get("/admin/orgs")
def a_orgs(user=Depends(require_admin)):
    with read() as conn:
        return {"orgs": admin.orgs(conn)}


@router.post("/admin/orgs")
def a_create_org(body: dict[str, Any], user=Depends(require_admin)):
    try:
        with tx() as conn:
            return {"id": admin.create_org(conn, user["id"], str(body.get("name") or ""), body.get("identifier"), body.get("notes"))}
    except (ValueError, Exception) as e:  # noqa: BLE001  (unique name)
        _bad(e)


@router.post("/admin/candidates/{cand_id}/assign")
def a_assign(cand_id: str, body: dict[str, Any], user=Depends(require_admin)):
    with tx() as conn:
        admin.assign(conn, user["id"], cand_id, int(body["org_id"]))
    return {"ok": True}


@router.post("/admin/scientific-reviews")
def a_sci(body: dict[str, Any], user=Depends(require_admin)):
    try:
        with tx() as conn:
            return {"id": admin.record_scientific_review(conn, user["id"], body)}
    except ValueError as e:
        _bad(e)


@router.get("/admin/config")
def a_config(user=Depends(require_admin)):
    with read() as conn:
        return {k: gconfig.get(conn, k) for k in gconfig.DEFAULTS}


@router.put("/admin/config/{key}")
def a_put_config(key: str, body: dict[str, Any], user=Depends(require_admin)):
    try:
        with tx() as conn:
            admin.put_config(conn, user["id"], key, body)
    except (KeyError, ValueError) as e:
        _bad(e)
    return {"ok": True}


@router.get("/admin/integrations")
def a_integrations(user=Depends(require_admin)):
    with read() as conn:
        return {"integrations": integration.configs(conn)}


@router.put("/admin/integrations/{key}")
def a_put_integration(key: str, body: dict[str, Any], user=Depends(require_admin)):
    try:
        with tx() as conn:
            integration.configure(conn, key, bool(body.get("enabled")), body.get("spec"), user["id"])
    except ValueError as e:
        _bad(e)
    return {"ok": True}


@router.get("/admin/export/{cand_id}")
def a_export(cand_id: str, user=Depends(require_admin)):
    try:
        with tx() as conn:
            pk = integration.record_export(conn, cand_id, user["id"], "json_export")
    except ValueError as e:
        _bad(e)
    body = json.dumps(pk["package"], sort_keys=True, indent=2, default=str)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{cand_id}.json"', "X-Package-SHA256": pk["sha256"]})


@router.get("/admin/audit")
def a_audit(limit: int = 200, user=Depends(require_admin)):
    with read() as conn:
        return {"audit": admin.audit_log(conn, min(limit, 1000))}


_ = scoring  # re-exported module used by tests
