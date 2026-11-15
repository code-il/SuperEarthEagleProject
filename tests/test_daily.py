"""Daily Survey: shared anchors, coverage-first real targets, hidden scoring, rooms, reliability."""
from __future__ import annotations

import numpy as np
import pytest

from backend.game import auth, config, contributions, daily, db, investigations

DAY = "2026-11-20"
TRAIN = [(f"Star {k}", cls) for k, cls in enumerate([0, 1, 2, 3, 4, 5, 8] * 4)]   # catalogued, class known


def fixture_catalog():
    rows = [(n, 20.0 + k, 10.0 + k, 1.0, 1.0, 6.0 + k * 0.1, cls, "catalogue", 10.0 + k, 2000 + k) for k, (n, cls) in enumerate(TRAIN)]
    rows += [(f"Uncharted {k}", 40.0 + k * 3, -20.0 + k, 0.0, 0.0, 16.0, 10, "none", 50.0 + k, 3000 + k) for k in range(30)]
    rows += [("Uncovered", 310.0, 0.0, 0.0, 0.0, 16.0, 10, "none", 1.0, 4000)]   # SPHEREx has no images here
    n = len(rows)
    col = lambda k: np.array([r[k] for r in rows])  # noqa: E731
    return {"name": col(0).astype(str), "ra": col(1).astype(float), "dec": col(2).astype(float), "pmra": col(3).astype(float),
            "pmdec": col(4).astype(float), "gmag": col(5).astype(float), "cls": col(6).astype(int), "src": col(7).astype(str),
            "dist": col(8).astype(float), "source_id": col(9).astype(np.int64), "prob": np.full(n, np.nan),
            "otype": np.array([""] * n), "sptype": np.array([""] * n), "plx": np.full(n, 50.0)}


def fake_search(ra, dec, fov=0.1, **kw):
    if ra > 300:
        return []
    return [{"observation_id": f"obs-{ra:.0f}-{k}", "observation_time": t, "band": 1, "detector": 1, "wavelength_min": 0.75,
             "wavelength_max": 1.1, "data_release": "QR", "mjd_start": 60796.0, "quality_flags": []}
            for k, t in ((1, "2025-05-01T00:00:00Z"), (2, "2025-11-01T00:00:00Z"))]


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("SPHEREX_GAME_DB", str(tmp_path / "game.db"))
    db.reset_for_tests()
    investigations.set_sources(fixture_catalog, fake_search)
    monkeypatch.setattr(daily, "today", lambda: DAY)
    c = db.connect()
    c.execute("BEGIN IMMEDIATE")
    yield c
    c.execute("ROLLBACK")
    c.close()
    investigations.set_sources(None, None)


def user(conn, name):
    uid = auth.register(conn, name, "correct horse", None)
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def items(conn, uid):
    """Attach observations to every target (the API does this per target) and return the raw rows."""
    for _ in range(3):
        rows = conn.execute("SELECT * FROM daily_items WHERE day=? AND user_id=? ORDER BY slot", (daily.today(), uid)).fetchall()
        for r in rows:
            inv = conn.execute("SELECT * FROM investigations WHERE id=?", (r["investigation_id"],)).fetchone()
            if investigations.attach_observations(conn, inv)["status"] == "data_unavailable" and r["status"] == "open":
                daily.replace_unavailable_anchors(conn) or daily.swap_unavailable(conn, uid, r["slot"])
    return conn.execute("SELECT d.*, i.ra, i.data FROM daily_items d JOIN investigations i ON i.id=d.investigation_id "
                        "WHERE d.day=? AND d.user_id=? ORDER BY d.slot", (daily.today(), uid)).fetchall()


def payload(ra, label):
    return {"label": label, "confidence": 0.7, "observations": [f"obs-{ra:.0f}-1", f"obs-{ra:.0f}-2"], "notes": "colours"}


def truth(row):
    return db.uj(row["data"], {}).get("training_answer")


def test_grading():
    assert daily.grade("k", "k") == "exact"
    assert daily.grade("g", "k") == "near" and daily.grade("m", "k") == "near"
    assert daily.grade("ob", "k") == "miss"
    assert daily.grade("giant", "k") == "near" and daily.grade("k", "giant") == "near"
    assert daily.grade("giant", "a") == "miss"
    assert daily.grade(None, "k") == "skipped"


def test_everyone_shares_anchors_but_real_targets_spread(conn):
    a, b = user(conn, "alice"), user(conn, "bob")
    daily.start(conn, a)
    daily.start(conn, b)
    ia, ib = items(conn, a["id"]), items(conn, b["id"])
    cfg = config.get(conn, "daily")
    assert len(ia) == cfg["anchors"] + cfg["real"]
    anchors_a = {r["investigation_id"] for r in ia if r["anchor"]}
    anchors_b = {r["investigation_id"] for r in ib if r["anchor"]}
    assert anchors_a == anchors_b and len(anchors_a) == cfg["anchors"]
    assert len({truth(r) for r in ia if r["anchor"]}) == cfg["anchors"]          # different classes
    real_a = {r["investigation_id"] for r in ia if not r["anchor"]}
    real_b = {r["investigation_id"] for r in ib if not r["anchor"]}
    assert not real_a & real_b                                                  # coverage spreads
    # anchors are reserved: never offered in normal discovery
    listed = {i["id"] for i in investigations.discover(conn, a["id"], "classification", limit=200)}
    assert not anchors_a & listed


def test_targets_reveal_nothing_until_finished(conn):
    a = user(conn, "alice")
    v = daily.start(conn, a)
    for it in v["run"]["items"]:
        assert "anchor" not in it and "answer" not in it and "grade" not in it
        assert it["label"].startswith("Target ") and "name" not in it and "data" not in it
    rows = items(conn, a["id"])
    r = rows[0]
    out = daily.answer(conn, a, r["slot"], payload(r["ra"], "k"))
    assert out["accepted"] and set(out) == {"slot", "accepted", "qc_status", "warnings", "achievements", "finished"}


def test_incomplete_answer_is_not_recorded(conn):
    a = user(conn, "alice")
    daily.start(conn, a)
    r = items(conn, a["id"])[0]
    bad = daily.answer(conn, a, r["slot"], {"label": "k", "confidence": 0.5, "observations": []})
    assert not bad["accepted"] and bad["missing"]
    assert conn.execute("SELECT COUNT(*) FROM contributions WHERE user_id=?", (a["id"],)).fetchone()[0] == 0
    assert daily.answer(conn, a, r["slot"], payload(r["ra"], "k"))["accepted"]   # can still answer properly
    with pytest.raises(daily.DailyError):
        daily.answer(conn, a, r["slot"], payload(r["ra"], "k"))


def test_full_run_scores_ranks_and_shares(conn):
    a, b = user(conn, "alice"), user(conn, "bob")
    for u, perfect in ((a, True), (b, False)):
        daily.start(conn, u)
        for r in items(conn, u["id"]):
            label = truth(r) if (r["anchor"] and perfect) else ("bd" if r["anchor"] else "m")
            daily.answer(conn, u, r["slot"], payload(r["ra"], label))
    va, vb = daily.view(conn, a["id"]), daily.view(conn, b["id"])
    cfg = config.get(conn, "daily")
    assert va["run"]["score"] == va["run"]["max_score"] == cfg["anchors"] * cfg["exact_points"]
    assert vb["run"]["score"] < va["run"]["score"]
    assert va["run"]["tiles"] == "🟩" * cfg["anchors"]
    assert va["run"]["surveyed"] == cfg["real"]
    assert va["run"]["rank"] == 1 and vb["run"]["rank"] == 2
    assert f"#{va['number']}" in va["run"]["share"] and "real sky coordinates" in va["run"]["share"]
    revealed = [i for i in va["run"]["items"] if i.get("anchor")]
    assert len(revealed) == cfg["anchors"] and all(i["answer"] for i in revealed)
    assert va["run"]["contribution_points"] > 0
    board = daily.leaderboard(conn, uid=a["id"])
    assert [e["name"] for e in board["entries"]] == ["alice", "bob"] and board["me"]["rank"] == 1
    assert daily.reliability(conn, a["id"])["score"] > daily.reliability(conn, b["id"])["score"]


def test_sources_stop_being_handed_out_once_classified_enough(conn):
    config.put(conn, "daily", {"votes_wanted": 1, "real": 4}, actor_id=0)
    config.put(conn, "pool", {"open_per_category": 5}, actor_id=0)
    seen: set[str] = set()
    for k in range(5):
        u = user(conn, f"player{k}")
        daily.start(conn, u)
        for r in items(conn, u["id"]):
            if not r["anchor"]:
                assert r["investigation_id"] not in seen, "a source with enough votes was handed out again"
                seen.add(r["investigation_id"])
            daily.answer(conn, u, r["slot"], payload(r["ra"], "m"))
    # 5 players x 4 real targets: the tiny pool was opened up automatically to cover new sky
    assert len(seen) == 20
    assert daily.view(conn, None)["community"]["coordinates_today"] == 20


def test_unobserved_real_target_is_swapped(conn):
    a = user(conn, "alice")
    daily.start(conn, a)
    rows = items(conn, a["id"])
    real = next(r for r in rows if not r["anchor"])
    conn.execute("UPDATE investigations SET status='data_unavailable' WHERE id=?", (real["investigation_id"],))
    assert daily.swap_unavailable(conn, a["id"], real["slot"])
    assert daily.item_investigation(conn, a["id"], real["slot"]) != real["investigation_id"]


def test_unobserved_anchor_is_replaced_for_everyone(conn):
    a, b = user(conn, "alice"), user(conn, "bob")
    daily.start(conn, a)
    daily.start(conn, b)
    old = daily.ensure_anchors(conn)[0]
    conn.execute("UPDATE investigations SET status='data_unavailable' WHERE id=?", (old,))
    assert daily.replace_unavailable_anchors(conn) == 1
    new = daily.ensure_anchors(conn)[0]
    assert new != old
    for u in (a, b):
        assert new in {r["investigation_id"] for r in items(conn, u["id"])}


def test_bonus_round_needs_finished_run_and_hides_anchors(conn):
    config.put(conn, "daily", {"bonus_anchor_every": 2}, actor_id=0)
    a = user(conn, "alice")
    daily.start(conn, a)
    with pytest.raises(daily.DailyError):
        daily.bonus(conn, a)
    for r in items(conn, a["id"]):
        daily.skip(conn, a["id"], r["slot"])
    assert daily.view(conn, a["id"])["run"]["finished_at"]
    kinds = []
    for _ in range(4):
        daily.bonus(conn, a)
        r = next(r for r in items(conn, a["id"]) if r["bonus"] and r["status"] == "open")
        kinds.append(r["anchor"])
        daily.answer(conn, a, r["slot"], payload(r["ra"], "k"))
    assert kinds == [0, 1, 0, 1]
    v = daily.view(conn, a["id"])
    assert v["run"]["score"] == 0                      # bonus anchors do not change the daily score
    assert v["reliability"]["anchors_answered"] == 2   # but they do measure accuracy


def test_rooms(conn):
    a, b, c = user(conn, "alice"), user(conn, "bob"), user(conn, "carol")
    room = daily.create_room(conn, a["id"], "Physics club")
    daily.join_room(conn, b["id"], room["code"].lower())
    for u in (a, b, c):
        daily.start(conn, u)
        for r in items(conn, u["id"]):
            daily.skip(conn, u["id"], r["slot"])
    board = daily.leaderboard(conn, room=room["code"], uid=b["id"])
    assert {e["name"] for e in board["entries"]} == {"alice", "bob"} and board["room_name"] == "Physics club"
    with pytest.raises(daily.DailyError):
        daily.leaderboard(conn, room=room["code"], uid=c["id"])
    assert daily.my_rooms(conn, b["id"])[0]["members"] == 2
    daily.leave_room(conn, b["id"], room["code"])
    assert daily.my_rooms(conn, b["id"]) == []


def test_weighted_consensus_recorded(conn):
    a = user(conn, "alice")
    daily.start(conn, a)
    r = next(r for r in items(conn, a["id"]) if not r["anchor"])
    daily.answer(conn, a, r["slot"], payload(r["ra"], "m"))
    cand = conn.execute("SELECT summary FROM candidates WHERE investigation_id=?", (r["investigation_id"],)).fetchone()
    s = db.uj(cand["summary"], {})
    assert s["weighted_consensus_label"] == "m" and s["weighted_consensus_fraction"] == 1.0


def test_daily_streak(conn, monkeypatch):
    a = user(conn, "alice")
    for d in ("2026-11-18", "2026-11-19", DAY):
        monkeypatch.setattr(daily, "today", lambda d=d: d)
        daily.start(conn, a)
        for r in conn.execute("SELECT slot FROM daily_items WHERE day=? AND user_id=? AND status='open'", (d, a["id"])).fetchall():
            daily.skip(conn, a["id"], r["slot"])
    assert daily.streak(conn, a["id"]) == 3
