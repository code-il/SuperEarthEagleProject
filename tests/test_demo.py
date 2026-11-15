"""Demo players: fill the boards, are marked, never touch the science pipeline, can be removed."""
from __future__ import annotations

from backend.game import daily, db, demo, leaderboards
from tests.test_daily import conn, user  # noqa: F401  (fixture)


def test_seed_fills_boards_and_marks_demo_rows(conn):  # noqa: F811
    r = demo.seed(conn)
    assert r["players"] == len(demo.PLAYERS) and r["events"] > 0 and r["daily_runs"] > 0
    board = leaderboards.contribution(conn, "all")
    assert board["entries"] and all(e["demo"] for e in board["entries"]) and board["includes_demo"]
    assert any(e["valid_contributions"] > 0 for e in board["entries"])
    assert leaderboards.contribution(conn, "week")["entries"]
    dboard = daily.leaderboard(conn)
    assert dboard["entries"] and all(e["demo"] and len(e["tiles"]) == 3 for e in dboard["entries"])
    # science pipeline and community counters stay real
    assert conn.execute("SELECT COUNT(*) FROM contributions").fetchone()[0] == 0
    assert daily.community(conn, daily.today())["players_today"] == 0
    assert leaderboards.progress(conn)["totals"]["investigators"] == 0


def test_seed_is_idempotent_and_real_players_rank_alongside(conn):  # noqa: F811
    demo.seed(conn)
    n = conn.execute("SELECT COUNT(*) FROM score_events").fetchone()[0]
    demo.seed(conn)
    assert conn.execute("SELECT COUNT(*) FROM score_events").fetchone()[0] == n
    a = user(conn, "alice")
    daily.start(conn, a)
    for r in conn.execute("SELECT slot FROM daily_items WHERE day=? AND user_id=?", (daily.today(), a["id"])).fetchall():
        daily.skip(conn, a["id"], r["slot"])
    entries = daily.leaderboard(conn, uid=a["id"])["entries"]
    me = next(e for e in entries if e["me"])
    assert not me["demo"]


def test_clear_removes_everything(conn, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SPHEREX_GAME_DEMO", "1")
    demo.ensure_today(conn)
    assert demo.clear(conn) == len(demo.PLAYERS)
    assert leaderboards.contribution(conn, "all")["entries"] == []
    assert daily.leaderboard(conn)["entries"] == []
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    _ = db
