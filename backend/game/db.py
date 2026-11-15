"""Persistent state of the citizen-science game engine (SQLite, standard library only).

One file database (``SPHEREX_GAME_DB``, default ``cache/game/game.db``), WAL mode, one connection
per request. Every write that touches scores, statuses or rewards runs in a transaction
(``tx()``) so a request either applies fully or not at all. Rewards are idempotent: each score
event carries a unique ``idem_key``, so retrying a request can never pay twice.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
_init_lock = threading.Lock()
_initialised: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw_hash TEXT NOT NULL,
  pw_salt TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'player' CHECK (role IN ('player','reviewer','admin')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
  display_name TEXT,
  bio TEXT,
  tz_name TEXT,
  tz_offset_min INTEGER,
  tz_changed_at TEXT,
  specialisations TEXT NOT NULL DEFAULT '[]',
  share_attribution INTEGER NOT NULL DEFAULT 0,
  org_id INTEGER REFERENCES review_orgs(id),
  pseudonym TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS investigations (
  id TEXT PRIMARY KEY,
  category TEXT NOT NULL CHECK (category IN ('moving','classification','image_quality','spectral','verification')),
  source_ref TEXT NOT NULL,
  source_name TEXT,
  ra REAL NOT NULL,
  dec REAL NOT NULL,
  difficulty INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','data_unavailable','closed','disabled')),
  training INTEGER NOT NULL DEFAULT 0,
  instructions TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  candidate_id TEXT,
  obs_refs TEXT,
  obs_checked_at TEXT,
  n_submissions INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inv_cat_status ON investigations(category, status, n_submissions);
CREATE UNIQUE INDEX IF NOT EXISTS ux_inv_source ON investigations(category, source_ref);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY,
  investigation_id TEXT NOT NULL REFERENCES investigations(id),
  category TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  ra REAL NOT NULL,
  dec REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'collecting' CHECK (status IN
    ('collecting','community_review','evidence_sufficient','awaiting_scientific_review','scientifically_reviewed')),
  summary TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (investigation_id)
);
CREATE TABLE IF NOT EXISTS contributions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  investigation_id TEXT NOT NULL REFERENCES investigations(id),
  candidate_id TEXT REFERENCES candidates(id),
  category TEXT NOT NULL,
  payload TEXT NOT NULL,
  obs_refs TEXT NOT NULL DEFAULT '[]',
  submitted_at TEXT NOT NULL,
  qc_status TEXT NOT NULL DEFAULT 'pending' CHECK (qc_status IN
    ('pending','passed','passed_with_warnings','incomplete','duplicate','rejected')),
  qc TEXT NOT NULL DEFAULT '{}',
  science_status TEXT NOT NULL DEFAULT 'not_submitted' CHECK (science_status IN
    ('not_submitted','awaiting_review','validated','not_validated')),
  hidden INTEGER NOT NULL DEFAULT 0,
  training INTEGER NOT NULL DEFAULT 0,
  dedupe_key TEXT,
  UNIQUE (user_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS ix_con_user ON contributions(user_id, submitted_at);
CREATE INDEX IF NOT EXISTS ix_con_cand ON contributions(candidate_id);
CREATE TABLE IF NOT EXISTS contribution_history (
  id INTEGER PRIMARY KEY,
  contribution_id TEXT NOT NULL REFERENCES contributions(id),
  at TEXT NOT NULL,
  field TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  actor TEXT NOT NULL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS review_sessions (
  candidate_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  opened_at TEXT NOT NULL,
  PRIMARY KEY (candidate_id, user_id)
);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  reviewer_id INTEGER NOT NULL REFERENCES users(id),
  assessment TEXT NOT NULL,
  independent_label TEXT NOT NULL,
  agrees INTEGER NOT NULL,
  reason_code TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (candidate_id, reviewer_id)
);
CREATE TABLE IF NOT EXISTS score_events (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  board TEXT NOT NULL CHECK (board IN ('contribution','participation')),
  kind TEXT NOT NULL,
  points REAL NOT NULL,
  category TEXT,
  ref_type TEXT,
  ref_id TEXT,
  idem_key TEXT NOT NULL UNIQUE,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_score_board ON score_events(board, created_at, user_id);
CREATE INDEX IF NOT EXISTS ix_score_user ON score_events(user_id, kind, created_at);
CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
  day TEXT NOT NULL,
  view TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  rank INTEGER NOT NULL,
  PRIMARY KEY (day, view, user_id)
);
CREATE TABLE IF NOT EXISTS login_days (
  user_id INTEGER NOT NULL REFERENCES users(id),
  local_date TEXT NOT NULL,
  utc_at TEXT NOT NULL,
  tz_offset_min INTEGER NOT NULL,
  PRIMARY KEY (user_id, local_date)
);
CREATE TABLE IF NOT EXISTS streaks (
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  current INTEGER NOT NULL DEFAULT 0,
  longest INTEGER NOT NULL DEFAULT 0,
  last_date TEXT,
  total_days INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS streak_milestones (
  user_id INTEGER NOT NULL,
  days INTEGER NOT NULL,
  reached_at TEXT NOT NULL,
  PRIMARY KEY (user_id, days)
);
CREATE TABLE IF NOT EXISTS achievements (
  key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  icon TEXT NOT NULL,
  rarity TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('participation','scientific')),
  requirement TEXT NOT NULL,
  reward_xp INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS player_achievements (
  user_id INTEGER NOT NULL,
  key TEXT NOT NULL REFERENCES achievements(key),
  unlocked_at TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS daily_challenges (
  day TEXT NOT NULL,
  key TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  criteria TEXT NOT NULL,
  reward_xp INTEGER NOT NULL,
  PRIMARY KEY (day, key)
);
CREATE TABLE IF NOT EXISTS challenge_completions (
  user_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  key TEXT NOT NULL,
  ref_id TEXT,
  completed_at TEXT NOT NULL,
  PRIMARY KEY (user_id, day, key)
);
CREATE TABLE IF NOT EXISTS daily_anchors (
  day TEXT NOT NULL,
  slot INTEGER NOT NULL,
  investigation_id TEXT NOT NULL REFERENCES investigations(id),
  PRIMARY KEY (day, slot)
);
CREATE TABLE IF NOT EXISTS daily_runs (
  day TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  score INTEGER NOT NULL DEFAULT 0,
  max_score INTEGER NOT NULL DEFAULT 0,
  surveyed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, user_id)
);
CREATE TABLE IF NOT EXISTS daily_items (
  day TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id),
  slot INTEGER NOT NULL,
  investigation_id TEXT NOT NULL REFERENCES investigations(id),
  anchor INTEGER NOT NULL DEFAULT 0,
  anchor_slot INTEGER,
  bonus INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','answered','skipped')),
  contribution_id TEXT REFERENCES contributions(id),
  qc_status TEXT,
  label TEXT,
  grade TEXT,
  answered_at TEXT,
  PRIMARY KEY (day, user_id, slot)
);
CREATE INDEX IF NOT EXISTS ix_daily_items_inv ON daily_items(investigation_id, status);
CREATE TABLE IF NOT EXISTS rooms (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS room_members (
  code TEXT NOT NULL REFERENCES rooms(code),
  user_id INTEGER NOT NULL REFERENCES users(id),
  joined_at TEXT NOT NULL,
  PRIMARY KEY (code, user_id)
);
CREATE TABLE IF NOT EXISTS review_orgs (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  identifier TEXT,
  notes TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_assignments (
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  org_id INTEGER NOT NULL REFERENCES review_orgs(id),
  status TEXT NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned','completed','withdrawn')),
  assigned_at TEXT NOT NULL,
  assigned_by INTEGER NOT NULL,
  PRIMARY KEY (candidate_id, org_id)
);
CREATE TABLE IF NOT EXISTS scientific_reviews (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  contribution_id TEXT REFERENCES contributions(id),
  org_id INTEGER NOT NULL REFERENCES review_orgs(id),
  reviewer_user_id INTEGER REFERENCES users(id),
  reviewer_identifier TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('expert_classification','accepted_candidate',
    'known_object_confirmed','recognised','not_validated')),
  comments TEXT,
  evidence TEXT NOT NULL DEFAULT '[]',
  external_ref TEXT,
  reviewed_at TEXT NOT NULL,
  simulated INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integration_configs (
  key TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('export','import','webhook','api_client')),
  enabled INTEGER NOT NULL DEFAULT 0,
  spec TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL,
  updated_by INTEGER
);
CREATE TABLE IF NOT EXISTS integration_events (
  id INTEGER PRIMARY KEY,
  config_key TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  idem_key TEXT NOT NULL UNIQUE,
  payload TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by INTEGER
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  actor_id INTEGER,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  details TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit_log(at);
"""


def db_path() -> Path:
    """SPHEREX_GAME_DB if set, else <cache dir>/game/game.db, where the cache dir is the same one the
    archive layer uses (SPHEREX_CACHE_DIR, default ./cache), so Docker keeps it on the writable volume."""
    if os.environ.get("SPHEREX_GAME_DB"):
        return Path(os.environ["SPHEREX_GAME_DB"])
    return Path(os.environ.get("SPHEREX_CACHE_DIR") or ROOT / "cache") / "game" / "game.db"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(5).upper()}"


def connect() -> sqlite3.Connection:
    p = db_path()
    key = str(p)
    if key not in _initialised:
        with _init_lock:
            if key not in _initialised:
                p.parent.mkdir(parents=True, exist_ok=True)
                c = sqlite3.connect(p)
                c.executescript("PRAGMA journal_mode=WAL;" + SCHEMA)
                c.commit()
                c.close()
                _initialised.add(key)
                from . import achievements, integration  # seed static rows once
                with tx() as conn:
                    achievements.seed(conn)
                    integration.seed(conn)
    conn = sqlite3.connect(p, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """A write transaction (BEGIN IMMEDIATE ... COMMIT, or ROLLBACK on error)."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def j(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def uj(s: str | None, default: Any = None) -> Any:
    return default if s is None else json.loads(s)


def audit(conn: sqlite3.Connection, actor_id: int | None, action: str, target_type: str | None = None,
          target_id: Any = None, details: dict | None = None) -> None:
    conn.execute("INSERT INTO audit_log(at, actor_id, action, target_type, target_id, details) VALUES (?,?,?,?,?,?)",
                 (now(), actor_id, action, target_type, None if target_id is None else str(target_id), j(details or {})))


def reset_for_tests() -> None:
    _initialised.clear()
