"""Fill (or empty) the leaderboards with demo players.

    python -m scripts.seed_demo           # add/extend demo players (usernames start with demo_)
    python -m scripts.seed_demo --clear   # remove every demo player and everything they own

Demo players have score-ledger events, streaks and Daily Survey runs only: no contributions,
candidates or reviews, so no science output or community total ever includes them.
"""
from __future__ import annotations

import argparse

from backend.game import db, demo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clear", action="store_true", help="remove all demo players")
    a = ap.parse_args(argv)
    with db.tx() as conn:
        if a.clear:
            print(f"Removed {demo.clear(conn)} demo players.")
        else:
            r = demo.seed(conn)
            print(f"Demo players: {r['players']} · new ledger events: {r['events']} · new Daily Survey runs: {r['daily_runs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
