"""Configurable game rules (admin-editable, stored in the ``config`` table; defaults here)."""
from __future__ import annotations

import copy
import sqlite3
from typing import Any

from .db import audit, j, now, uj

DEFAULTS: dict[str, Any] = {
    # Contribution points (Global Contribution Leaderboard). Only awarded after automated checks.
    "scoring": {
        "valid_investigation": 10,
        "possible_moving_source": 15,
        "valid_classification": 20,
        "independent_review": 20,
        "high_quality_evidence": 30,
        "strong_agreement": 25,
        "verification_task": 10,
        "unclassified_bonus": 5,
        # Diminishing returns per (activity, category) per UTC day: full value for the first
        # `full_per_day` events, then multiplied by each factor in turn, then `floor_factor`.
        "full_per_day": 15,
        "decay_factors": [0.5, 0.25],
        "decay_block": 10,
        "floor_factor": 0.1,
        "passed_with_warnings_factor": 0.75,
    },
    # Participation XP (levels and profile only; never counts on the contribution leaderboard).
    "participation": {
        "daily_login": 5,
        "training_correct": 5,
        "training_attempt": 2,
        "challenge_default": 15,
        "streak_milestone_bonus": {"3": 10, "7": 25, "14": 40, "30": 75, "60": 120, "100": 200},
    },
    "levels": {
        "thresholds": [0, 50, 150, 300, 500, 800, 1200, 1700, 2300, 3000, 4000, 5500, 7500, 10000],
        "titles": ["Observer", "Stargazer", "Field Analyst", "Survey Analyst", "Astrometrist",
                   "Spectroscopist", "Senior Investigator", "Lead Investigator", "Principal Investigator",
                   "Survey Scientist", "Senior Survey Scientist", "Distinguished Investigator",
                   "Fellow", "Citizen Science Laureate"],
    },
    "review": {
        "min_seconds_before_submit": 15,
        "strong_agreement_reviews": 3,
        "strong_agreement_fraction": 0.75,
        "reviews_wanted": 3,
    },
    "pool": {"open_per_category": 40, "training_fraction": 0.2},
    "quality": {"footprint_radius_deg": 0.08, "significance_sigma": 3.0, "min_unc_arcsec": 1.0,
                "high_quality_evidence": 0.8},
}


def get(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    base = copy.deepcopy(DEFAULTS.get(key, {}))
    if row:
        stored = uj(row["value"], {})
        if isinstance(base, dict) and isinstance(stored, dict):
            base.update(stored)
        else:
            base = stored
    return base


def put(conn: sqlite3.Connection, key: str, value: Any, actor_id: int) -> Any:
    if key not in DEFAULTS:
        raise KeyError(key)
    if not isinstance(value, dict):
        raise ValueError("config values are objects")
    unknown = set(value) - set(DEFAULTS[key])
    if unknown:
        raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
    before = get(conn, key)
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    value = {**(uj(row["value"], {}) if row else {}), **value}
    conn.execute("INSERT INTO config(key, value, updated_at, updated_by) VALUES (?,?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                 (key, j(value), now(), actor_id))
    audit(conn, actor_id, "config.update", "config", key, {"before": before, "after": get(conn, key)})
    return get(conn, key)
