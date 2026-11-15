"""Accounts and sessions. The app had no authentication before; this adds a minimal one.

Passwords: scrypt (standard library), per-user salt, constant-time comparison.
Sessions: random bearer tokens; only their SHA-256 is stored. 30-day expiry.
Roles: player (default), reviewer (external scientific reviewer, tied to an organisation),
admin. Usernames listed in ``SPHEREX_GAME_ADMINS`` are made admins when they register.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from .db import audit, now

USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
SESSION_DAYS = 30


class AuthError(Exception):
    pass


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()


def register(conn: sqlite3.Connection, username: str, password: str, display_name: str | None = None) -> int:
    if not USERNAME.match(username or ""):
        raise AuthError("Username: 3–32 letters, digits, '.', '_' or '-'.")
    if len(password or "") < 8:
        raise AuthError("Password: at least 8 characters.")
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise AuthError("That username is taken.")
    salt = secrets.token_bytes(16)
    admins = {u.strip().lower() for u in os.environ.get("SPHEREX_GAME_ADMINS", "").split(",") if u.strip()}
    role = "admin" if username.lower() in admins else "player"
    cur = conn.execute(
        "INSERT INTO users(username, pw_hash, pw_salt, role, display_name, pseudonym, created_at) VALUES (?,?,?,?,?,?,?)",
        (username, _hash(password, salt), salt.hex(), role, (display_name or username)[:60],
         "INV-" + secrets.token_hex(4).upper(), now()))
    uid = int(cur.lastrowid)
    conn.execute("INSERT INTO streaks(user_id) VALUES (?)", (uid,))
    audit(conn, uid, "user.register", "user", uid, {"role": role})
    return uid


def login(conn: sqlite3.Connection, username: str, password: str) -> str:
    row = conn.execute("SELECT * FROM users WHERE username=?", (username or "",)).fetchone()
    # Same work whether or not the user exists (no username probing by timing).
    calc = _hash(password or "", bytes.fromhex(row["pw_salt"]) if row else b"0" * 16)
    if not row or not hmac.compare_digest(calc, row["pw_hash"]):
        raise AuthError("Wrong username or password.")
    if row["status"] != "active":
        raise AuthError("This account is suspended.")
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (hashlib.sha256(token.encode()).hexdigest(), row["id"], now(), exp))
    return token


def logout(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))


def user_for_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=? AND s.expires_at > ?",
        (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
    if row is None or row["status"] != "active":
        return None
    return row
