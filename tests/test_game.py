"""Citizen-science game engine: security, scoring, streaks, QA, review, leaderboards, integration.

Runs offline against a small fixture catalogue and a fake archive (two real-format SPHEREx
observation records per field), each test with a fresh database.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from backend.game import (achievements, admin, auth, challenges, config, contributions, db, integration, investigations,
                          leaderboards, players, quality, reviews, scoring)

T1, T2 = "2025-05-01T00:00:00Z", "2025-11-01T00:00:00Z"


def _year(t):
    return quality._year(t)


def fixture_catalog():
    rows = [
        # name, ra, dec, pmra, pmdec, gmag, cls, src, dist, source_id
        ("Uncharted A", 10.0, 20.0, 0.0, 0.0, 15.0, 10, "none", 30.0, 1001),
        ("Uncharted B", 11.0, 21.0, 0.0, 0.0, 15.5, 10, "none", 40.0, 1002),
        ("Train K", 12.0, 22.0, 10.0, 5.0, 8.0, 4, "catalogue", 20.0, 1003),
        ("Fast star", 100.0, 10.0, 10000.0, 0.0, 9.0, 5, "catalogue", 5.0, 1004),
        ("Brown dwarf", 150.0, -5.0, 300.0, 100.0, 19.0, 6, "catalogue", 8.0, 1005),
        ("Bright", 200.0, 30.0, 50.0, 50.0, 1.0, 2, "catalogue", 25.0, 1006),
    ]
    n = len(rows)
    col = lambda k: np.array([r[k] for r in rows])  # noqa: E731
    return {"name": col(0).astype(str), "ra": col(1).astype(float), "dec": col(2).astype(float), "pmra": col(3).astype(float),
            "pmdec": col(4).astype(float), "gmag": col(5).astype(float), "cls": col(6).astype(int), "src": col(7).astype(str),
            "dist": col(8).astype(float), "source_id": col(9).astype(np.int64), "prob": np.full(n, np.nan),
            "otype": np.array([""] * n), "sptype": np.array([""] * n), "plx": np.full(n, 50.0)}


def fake_search(ra, dec, fov=0.1, **kw):
    if ra > 300:   # a field with no SPHEREx coverage
        return []
    return [{"observation_id": f"obs-{ra:.0f}-1", "observation_time": T1, "band": 1, "detector": 1, "wavelength_min": 0.75,
             "wavelength_max": 1.1, "data_release": "QR", "mjd_start": 60796.0, "quality_flags": []},
            {"observation_id": f"obs-{ra:.0f}-2", "observation_time": T2, "band": 1, "detector": 1, "wavelength_min": 0.75,
             "wavelength_max": 1.1, "data_release": "QR", "mjd_start": 60980.0, "quality_flags": []}]


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("SPHEREX_GAME_DB", str(tmp_path / "game.db"))
    monkeypatch.setenv("SPHEREX_GAME_ADMINS", "boss")
    db.reset_for_tests()
    investigations.set_sources(fixture_catalog, fake_search)
    c = db.connect()
    c.execute("BEGIN IMMEDIATE")
    yield c
    c.execute("ROLLBACK")
    c.close()
    investigations.set_sources(None, None)


def user(conn, name="alice"):
    uid = auth.register(conn, name, "correct horse", None)
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def inv_of(conn, category, training=0, name=None):
    investigations.ensure_pool(conn, category)
    q = "SELECT * FROM investigations WHERE category=? AND training=?"
    args = [category, training]
    if name:
        q += " AND source_name=?"; args.append(name)
    inv = conn.execute(q, args).fetchone()
    return investigations.attach_observations(conn, inv)


def classify(conn, u, inv, label="m", **extra):
    p = {"label": label, "confidence": 0.8, "observations": [f"obs-{inv['ra']:.0f}-1", f"obs-{inv['ra']:.0f}-2"], "notes": "red, faint", **extra}
    return contributions.submit(conn, u, inv["id"], p)


def contrib_points(conn, uid):
    return scoring.totals(conn, uid)["contribution_xp"]


# ----------------------------------------------------------------------------- auth & security
def test_register_login_and_roles(conn):
    u = user(conn)
    assert u["role"] == "player" and u["pw_hash"] != "correct horse"
    tok = auth.login(conn, "alice", "correct horse")
    assert auth.user_for_token(conn, tok)["id"] == u["id"]
    with pytest.raises(auth.AuthError):
        auth.login(conn, "alice", "wrong password")
    with pytest.raises(auth.AuthError):
        auth.register(conn, "ALICE", "another one", None)      # usernames are case-insensitive
    with pytest.raises(auth.AuthError):
        auth.register(conn, "bob", "short", None)
    assert user(conn, "boss")["role"] == "admin"
    auth.logout(conn, tok)
    assert auth.user_for_token(conn, tok) is None


def test_suspended_user_cannot_log_in_or_submit(conn):
    u = user(conn)
    boss = user(conn, "boss")
    admin.set_user(conn, boss["id"], u["id"], status="suspended")
    with pytest.raises(auth.AuthError):
        auth.login(conn, "alice", "correct horse")
    u = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    with pytest.raises(contributions.SubmitError):
        classify(conn, u, inv_of(conn, "classification"))


def test_admin_cannot_demote_self(conn):
    boss = user(conn, "boss")
    with pytest.raises(ValueError):
        admin.set_user(conn, boss["id"], boss["id"], role="player")


def test_api_role_checks_and_no_client_scoring(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.api.main import app
    monkeypatch.setenv("SPHEREX_GAME_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("SPHEREX_GAME_ADMINS", "boss")
    db.reset_for_tests()
    investigations.set_sources(fixture_catalog, fake_search)
    try:
        c = TestClient(app)
        tok = c.post("/api/game/auth/register", json={"username": "carol", "password": "longenough"}).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/game/me").status_code == 401
        assert c.get("/api/game/admin/metrics", headers=h).status_code == 403
        assert c.put("/api/game/admin/config/scoring", headers=h, json={"valid_investigation": 9999}).status_code == 403
        inv = next(i for i in c.get("/api/game/investigations?category=classification", headers=h).json()["investigations"]
                   if i["kind"] == "real")
        full = c.get(f"/api/game/investigations/{inv['id']}", headers=h).json()
        obs = [o["id"] for o in full["observations"]]
        r = c.post(f"/api/game/investigations/{inv['id']}/submit", headers=h,
                   json={"label": "m", "confidence": 0.9, "observations": obs, "notes": "x", "points": 1e9, "qc_status": "passed"}).json()
        assert r["qc"]["status"] == "passed"
        assert r["totals"]["contribution_xp"] < 100          # client-sent "points" ignored
        board = c.get("/api/game/leaderboard").json()
        assert board["entries"][0]["name"] == "carol"
        admin_tok = c.post("/api/game/auth/register", json={"username": "boss", "password": "longenough"}).json()["token"]
        assert c.get("/api/game/admin/metrics", headers={"Authorization": f"Bearer {admin_tok}"}).status_code == 200
    finally:
        investigations.set_sources(None, None)


# ----------------------------------------------------------------------------- score ledger
def test_award_is_idempotent(conn):
    u = user(conn)
    assert scoring.award(conn, u["id"], "contribution", "x", 10, "k1") is not None
    assert scoring.award(conn, u["id"], "contribution", "x", 10, "k1") is None
    assert contrib_points(conn, u["id"]) == 10


def test_diminishing_returns(conn):
    u = user(conn)
    sc = config.get(conn, "scoring")
    pts = [scoring.award(conn, u["id"], "contribution", "rep", 10, f"r{i}", category="c")["points"]
           for i in range(sc["full_per_day"] + sc["decay_block"] * 2 + 1)]
    assert pts[sc["full_per_day"] - 1] == 10
    assert pts[sc["full_per_day"]] == 10 * sc["decay_factors"][0]
    assert pts[-1] == 10 * sc["floor_factor"]


def test_levels(conn):
    assert scoring.level_for(conn, 0)["level"] == 1
    assert scoring.level_for(conn, 49)["level"] == 1
    assert scoring.level_for(conn, 50)["level"] == 2
    top = scoring.level_for(conn, 1e9)
    assert top["next_level_at"] is None and top["progress"] is None


# ----------------------------------------------------------------------------- streaks
def at(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_streak_consecutive_gap_and_idempotent(conn):
    u = user(conn)
    r = players.checkin(conn, u, None, 0, at(2025, 1, 1))
    assert r["new_day"] and r["streak"]["current"] == 1
    assert players.checkin(conn, u, None, 0, at(2025, 1, 1, 20))["new_day"] is False
    for d in (2, 3):
        r = players.checkin(conn, u, None, 0, at(2025, 1, d))
    assert r["streak"]["current"] == 3 and 3 in r["milestones"]
    assert any(a["key"] == "first_explorer" for a in r["achievements"])
    r = players.checkin(conn, u, None, 0, at(2025, 1, 5))
    assert r["streak"]["current"] == 1 and r["streak"]["longest"] == 3
    login_xp = conn.execute("SELECT SUM(points) FROM score_events WHERE user_id=? AND kind='daily_login'", (u["id"],)).fetchone()[0]
    assert login_xp == 4 * config.get(conn, "participation")["daily_login"]
    assert contrib_points(conn, u["id"]) == 0          # login XP never reaches the contribution board


def test_streak_uses_local_calendar_day(conn):
    u = user(conn)
    # 23:30 UTC on 1 Jan is 2 Jan in UTC+2: that is the player's day.
    r = players.checkin(conn, u, None, 120, datetime(2025, 1, 1, 23, 30, tzinfo=timezone.utc))
    assert r["date"] == "2025-01-02"


def test_dst_change_keeps_streak(conn):
    u = user(conn)
    # Europe/London, spring forward 30 Mar 2025: offset goes 0 -> +60 overnight.
    players.checkin(conn, u, "Europe/London", 0, at(2025, 3, 29, 9))
    u = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    conn.execute("UPDATE users SET tz_changed_at='2025-03-01T00:00:00Z' WHERE id=?", (u["id"],))
    u = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    r = players.checkin(conn, u, "Europe/London", 60, at(2025, 3, 30, 9))
    assert r["date"] == "2025-03-30" and r["streak"]["current"] == 2


def test_timezone_hopping_is_limited(conn):
    u = user(conn)
    players.checkin(conn, u, None, 0, at(2025, 1, 1, 13))
    u = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    conn.execute("UPDATE users SET tz_changed_at=? WHERE id=?", ("2025-01-01T12:00:00Z", u["id"]))
    u = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    # Same instant, claim UTC+14 to make it "2 Jan": refused within 24 h of the last change.
    r = players.checkin(conn, u, None, 840, at(2025, 1, 1, 13, ))
    assert r["date"] == "2025-01-01" and r["new_day"] is False
    with pytest.raises(ValueError):
        players.checkin(conn, u, None, 9999, at(2025, 1, 1))


def test_westward_travel_does_not_break_streak(conn):
    u = user(conn)
    players.checkin(conn, u, None, 600, at(2025, 1, 1, 20))     # 2 Jan in UTC+10
    conn.execute("UPDATE users SET tz_changed_at='2024-01-01T00:00:00Z' WHERE id=?", (u["id"],))
    u = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    r = players.checkin(conn, u, None, -600, at(2025, 1, 2, 5))  # 1 Jan in UTC-10: an earlier date
    assert r["streak"]["current"] == 1 and r["streak"]["last_date"] == "2025-01-02"


# ----------------------------------------------------------------------------- investigations & QA
def test_pool_comes_from_catalogue(conn):
    investigations.ensure_pool(conn, "classification")
    rows = conn.execute("SELECT * FROM investigations WHERE category='classification'").fetchall()
    names = {r["source_name"] for r in rows}
    assert {"Uncharted A", "Uncharted B"} <= names
    tr = [r for r in rows if r["training"]]
    assert tr and json.loads(tr[0]["data"])["training_answer"] == "k"
    assert "training_answer" not in investigations.out(tr[0])["data"]
    assert "class_key" not in investigations.out(tr[0])["data"]
    investigations.ensure_pool(conn, "moving")
    mv = conn.execute("SELECT source_name FROM investigations WHERE category='moving'").fetchall()
    assert [r[0] for r in mv] == ["Fast star"]


def test_no_coverage_marks_data_unavailable(conn):
    conn.execute("INSERT INTO investigations(id, category, source_ref, source_name, ra, dec, status, instructions, created_at) "
                 "VALUES ('INV-X','image_quality','atlas:x','X',310,0,'open','x','2025-01-01T00:00:00Z')")
    inv = conn.execute("SELECT * FROM investigations WHERE id='INV-X'").fetchone()
    assert investigations.attach_observations(conn, inv)["status"] == "data_unavailable"


def test_archive_down_is_not_recorded_as_no_data(conn):
    def down(*a, **k):
        raise OSError("offline")
    investigations.set_sources(fixture_catalog, down)
    inv = inv_of(conn, "image_quality")
    assert inv["status"] == "open" and inv["obs_refs"] is None


def test_valid_classification_scores_and_duplicate_does_not(conn):
    u = user(conn)
    inv = inv_of(conn, "classification", name="Uncharted A")
    r = classify(conn, u, inv)
    assert r["qc"]["status"] == "passed"
    kinds = {e["kind"] for e in r["rewards"]}
    assert {"valid_investigation", "valid_classification", "unclassified_bonus"} <= kinds
    before = contrib_points(conn, u["id"])
    r2 = classify(conn, u, inv)
    assert r2["qc"]["status"] == "duplicate" and r2["rewards"] == []
    assert contrib_points(conn, u["id"]) == before
    hist = [h["to_status"] for h in contributions.get(conn, r["contribution_id"])["history"]]
    assert hist == ["pending", "passed"]


def test_fake_observation_is_rejected(conn):
    u = user(conn)
    inv = inv_of(conn, "classification", name="Uncharted A")
    r = contributions.submit(conn, u, inv["id"], {"label": "m", "confidence": 0.5, "observations": ["made-up-123"]})
    assert r["qc"]["status"] == "rejected" and r["rewards"] == []
    assert "Automated quality checks only" in r["qc"]["disclaimer"]


def test_missing_label_is_incomplete(conn):
    u = user(conn)
    inv = inv_of(conn, "classification", name="Uncharted A")
    r = contributions.submit(conn, u, inv["id"], {"confidence": 0.5, "observations": [f"obs-{inv['ra']:.0f}-1"]})
    assert r["qc"]["status"] == "incomplete"


def test_training_gives_participation_only(conn):
    u = user(conn)
    inv = inv_of(conn, "classification", training=1)
    r = classify(conn, u, inv, label="k")
    assert r["qc"]["training_correct"] is True
    assert all(e["board"] == "participation" for e in r["rewards"])
    assert contrib_points(conn, u["id"]) == 0
    assert conn.execute("SELECT candidate_id FROM contributions WHERE id=?", (r["contribution_id"],)).fetchone()[0] is None


def _moving_payload(inv, pm_mas=10000.0, extra_arcsec=0.0):
    data = json.loads(inv["data"])
    pts = []
    for oid, t in ((f"obs-{inv['ra']:.0f}-1", T1), (f"obs-{inv['ra']:.0f}-2", T2)):
        dt = _year(t) - 2016.0
        east = pm_mas * dt / 1000 + (extra_arcsec if t == T2 else 0)
        pts.append({"obs_id": oid, "ra": inv["ra"] + east / 3600 / math.cos(math.radians(inv["dec"])), "dec": inv["dec"], "unc_arcsec": 1.0})
    assert data["pmra_mas_yr"] == pm_mas
    return {"measurements": pts, "moved": True, "notes": "blinked"}


def test_moving_matches_known_proper_motion(conn):
    u = user(conn)
    inv = inv_of(conn, "moving")
    r = contributions.submit(conn, u, inv["id"], _moving_payload(inv))
    qc = r["qc"]
    assert qc["status"] == "passed", qc
    assert qc["significant_motion"] and qc["known_motion_consistent"]
    assert abs(qc["displacement_arcsec"] - qc["predicted_displacement_arcsec"]) < 0.05
    assert "possible_moving_source" in {e["kind"] for e in r["rewards"]}


def test_moving_inconsistent_with_gaia_is_flagged(conn):
    u = user(conn)
    inv = inv_of(conn, "moving")
    r = contributions.submit(conn, u, inv["id"], _moving_payload(inv, extra_arcsec=40.0))
    assert r["qc"]["status"] == "passed_with_warnings"
    assert r["qc"]["known_motion_consistent"] is False


def test_moving_outside_footprint_rejected(conn):
    u = user(conn)
    inv = inv_of(conn, "moving")
    p = _moving_payload(inv)
    p["measurements"][1]["dec"] = inv["dec"] + 1.0
    assert contributions.submit(conn, u, inv["id"], p)["qc"]["status"] == "rejected"


# ----------------------------------------------------------------------------- community review
def _open_review(conn, cand_id, uid):
    v = reviews.blind_view(conn, cand_id, uid)
    conn.execute("UPDATE review_sessions SET opened_at='2000-01-01T00:00:00Z' WHERE candidate_id=? AND user_id=?", (cand_id, uid))
    return v


def test_blind_review_flow_and_strong_agreement(conn):
    a = user(conn, "alice")
    inv = inv_of(conn, "classification", name="Uncharted A")
    r = classify(conn, a, inv, label="m")
    cand = conn.execute("SELECT * FROM candidates WHERE investigation_id=?", (inv["id"],)).fetchone()
    assert cand["status"] == "community_review"
    assert conn.execute("SELECT 1 FROM investigations WHERE category='verification' AND candidate_id=?", (cand["id"],)).fetchone()
    with pytest.raises(reviews.ReviewError):
        reviews.blind_view(conn, cand["id"], a["id"])          # no self-review
    before = contrib_points(conn, a["id"])
    for k, name in enumerate(("bob", "carl", "dana")):
        rv = user(conn, name)
        v = reviews.blind_view(conn, cand["id"], rv["id"])
        blob = json.dumps(v)
        assert "consensus" not in blob and all("label" not in e for e in v["evidence"])   # answer not revealed
        assert "alice" not in blob and v["evidence"][0]["submission"] == "Investigator A"
        with pytest.raises(reviews.ReviewError):
            reviews.submit(conn, rv, cand["id"], {"label": "m", "reason_code": "evidence_clear"})   # too fast
        conn.execute("UPDATE review_sessions SET opened_at='2000-01-01T00:00:00Z' WHERE candidate_id=? AND user_id=?", (cand["id"], rv["id"]))
        out = reviews.submit(conn, rv, cand["id"], {"label": "m", "reason_code": "evidence_clear"})
        assert out["agrees"] and out["revealed"]["consensus_label"] == "m"
        with pytest.raises(reviews.ReviewError):
            reviews.submit(conn, rv, cand["id"], {"label": "m", "reason_code": "evidence_clear"})   # once only
    st = reviews.stats(conn, cand["id"])
    assert st["reviews"] == 3 and st["agreement"] == 1.0 and 0 < st["agreement_lower_95"] < 1
    assert conn.execute("SELECT status FROM candidates WHERE id=?", (cand["id"],)).fetchone()[0] == "evidence_sufficient"
    assert contrib_points(conn, a["id"]) == before + config.get(conn, "scoring")["strong_agreement"]
    # still not scientifically validated
    assert leaderboards.validated(conn)["entries"] == []
    assert r["contribution_id"]


def test_disagreeing_review_recorded(conn):
    a, b = user(conn, "alice"), user(conn, "bob")
    inv = inv_of(conn, "classification", name="Uncharted A")
    classify(conn, a, inv, label="m")
    cand = conn.execute("SELECT id FROM candidates").fetchone()[0]
    _open_review(conn, cand, b["id"])
    out = reviews.submit(conn, b, conn.execute("SELECT * FROM candidates").fetchone()["id"],
                         {"label": "galaxy", "reason_code": "wrong_class"})
    assert out["agrees"] is False and reviews.stats(conn, cand)["disagree"] == 1


# ----------------------------------------------------------------------------- leaderboards
def test_global_leaderboard_periods_and_categories(conn):
    a, b = user(conn, "alice"), user(conn, "bob")
    scoring.award(conn, a["id"], "contribution", "x", 30, "a1", category="moving")
    scoring.award(conn, b["id"], "contribution", "x", 50, "b1", category="classification")
    scoring.award(conn, a["id"], "participation", "daily_login", 1000, "a2")      # never counts here
    conn.execute("INSERT INTO score_events(user_id, board, kind, points, idem_key, created_at) VALUES (?,?,?,?,?,?)",
                 (a["id"], "contribution", "x", 500, "old", "2000-01-01T00:00:00Z"))
    allb = leaderboards.contribution(conn, "all", uid=b["id"])
    assert [e["name"] for e in allb["entries"]] == ["alice", "bob"]
    assert allb["me"]["rank"] == 2 and len(allb["nearby"]) == 2
    week = leaderboards.contribution(conn, "week")
    assert [e["name"] for e in week["entries"]] == ["bob", "alice"]
    mv = leaderboards.contribution(conn, "all", "moving")
    assert [e["name"] for e in mv["entries"]] == ["alice"]


def test_suspended_users_leave_the_board(conn):
    a, boss = user(conn, "alice"), user(conn, "boss")
    scoring.award(conn, a["id"], "contribution", "x", 30, "a1")
    admin.set_user(conn, boss["id"], a["id"], status="suspended")
    assert leaderboards.contribution(conn)["entries"] == []


def test_validated_board_real_vs_simulated(conn):
    a, boss = user(conn, "alice"), user(conn, "boss")
    inv = inv_of(conn, "classification", name="Uncharted A")
    classify(conn, a, inv)
    cand = conn.execute("SELECT id FROM candidates").fetchone()[0]
    org = admin.create_org(conn, boss["id"], "Example Observatory", "EXO", None)
    with pytest.raises(ValueError):
        admin.record_scientific_review(conn, boss["id"], {"candidate_id": cand, "org_id": org, "outcome": "recognised"})
    with pytest.raises(ValueError):   # real records need provenance
        admin.record_scientific_review(conn, boss["id"], {"candidate_id": cand, "org_id": org, "outcome": "recognised", "simulated": False})
    admin.record_scientific_review(conn, boss["id"], {"candidate_id": cand, "org_id": org, "outcome": "recognised", "simulated": True})
    assert leaderboards.validated(conn)["entries"] == []
    sim = leaderboards.validated(conn, simulated=True)
    assert sim["entries"][0]["name"] == "alice" and "SIMULATED" in sim["note"]
    assert not any(x["key"] == "cosmic_pioneer" for x in players.profile(conn, a["id"])["achievements"])
    admin.record_scientific_review(conn, boss["id"], {"candidate_id": cand, "org_id": org, "outcome": "expert_classification",
                                                      "simulated": False, "reviewer_identifier": "Dr X", "external_ref": "EXO-2025-17"})
    assert leaderboards.validated(conn)["entries"][0]["name"] == "alice"
    assert any(x["key"] == "cosmic_pioneer" for x in players.profile(conn, a["id"])["achievements"])
    assert conn.execute("SELECT science_status FROM contributions").fetchone()[0] == "validated"


# ----------------------------------------------------------------------------- moderation, challenges, integration
def test_moderation_reject_reverses_points(conn):
    a, boss = user(conn, "alice"), user(conn, "boss")
    r = classify(conn, a, inv_of(conn, "classification", name="Uncharted A"))
    assert contrib_points(conn, a["id"]) > 0
    with pytest.raises(ValueError):
        admin.moderate_contribution(conn, boss["id"], r["contribution_id"], "reject", "")
    admin.moderate_contribution(conn, boss["id"], r["contribution_id"], "reject", "copied answer")
    assert contrib_points(conn, a["id"]) == 0
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='moderation.reject'").fetchone()[0] == 1


def test_challenge_needs_a_qualifying_contribution(conn):
    a = user(conn)
    inv = inv_of(conn, "classification", name="Uncharted A")
    investigations.ensure_pool(conn, "moving")
    keys = {c["key"] for c in challenges.ensure_today(conn)}
    players.checkin(conn, a, None, 0)
    assert not any(c["completed"] for c in challenges.for_user(conn, a["id"]))
    r = classify(conn, a, inv)
    if "unclassified" in keys:
        assert any(e.get("challenge") == "Classify an uncharted source" for e in r["rewards"])
        assert all(e["board"] == "participation" for e in r["rewards"] if e.get("challenge"))


def test_integrations_off_by_default_and_signed(conn):
    assert all(not c["enabled"] for c in integration.configs(conn))
    with pytest.raises(PermissionError):
        integration.receive_review(conn, b'{"id": "1"}', "sha256=00")
    boss = user(conn, "boss")
    with pytest.raises(ValueError):
        integration.configure(conn, "outbound_webhook", True, {"url": "http://insecure.example"}, boss["id"])
    integration.configure(conn, "inbound_review_webhook", True, {"secret": "s3cret"}, boss["id"])
    body = b'{"id": "msg-1", "outcome": "recognised"}'
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    with pytest.raises(PermissionError):
        integration.receive_review(conn, body, "sha256=bad")
    assert integration.receive_review(conn, body, sig)["status"] == "queued_for_admin"
    assert integration.receive_review(conn, body, sig)["status"] == "duplicate"
    assert conn.execute("SELECT COUNT(*) FROM scientific_reviews").fetchone()[0] == 0   # never auto-applied


def test_export_package_is_deterministic_and_pseudonymous(conn):
    a = user(conn, "alice")
    classify(conn, a, inv_of(conn, "classification", name="Uncharted A"))
    cand = conn.execute("SELECT id FROM candidates").fetchone()[0]
    p1, p2 = integration.package(conn, cand), integration.package(conn, cand)
    assert p1["sha256"] == p2["sha256"]
    blob = json.dumps(p1["package"])
    assert "alice" not in blob and a["pseudonym"] in blob
    assert "not scientific validation" in p1["package"]["disclaimer"]
    players.update_profile(conn, a["id"], {"share_attribution": True})
    assert "alice" in json.dumps(integration.package(conn, cand)["package"])


def test_config_is_validated_and_audited(conn):
    boss = user(conn, "boss")
    with pytest.raises(ValueError):
        config.put(conn, "scoring", {"not_a_setting": 1}, boss["id"])
    config.put(conn, "scoring", {"valid_investigation": 12}, boss["id"])
    config.put(conn, "scoring", {"independent_review": 21}, boss["id"])
    sc = config.get(conn, "scoring")
    assert sc["valid_investigation"] == 12 and sc["independent_review"] == 21
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='config.update'").fetchone()[0] == 2


def test_achievement_unlocks_once(conn):
    a = user(conn)
    classify(conn, a, inv_of(conn, "classification", name="Uncharted A"))
    assert conn.execute("SELECT COUNT(*) FROM player_achievements WHERE key='first_light'").fetchone()[0] == 1
    assert achievements.evaluate(conn, a["id"]) == []
