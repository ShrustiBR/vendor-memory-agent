"""Simple account system: sign up, log in. Passwords are salted PBKDF2 hashes in SQLite.

Stdlib only. DB path: env USERS_DB_PATH (default data/users.db).
NOTE: on hosts with an ephemeral disk (e.g. Streamlit Community Cloud) this file is wiped on
restart/redeploy. For permanent accounts, swap this module for Supabase/Firebase/Auth0.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ITERATIONS = 200_000
MAX_FAILS = 5
LOCK_SECONDS = 60
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")


class AuthError(ValueError):
    """Raised with a message that is safe to show to the user."""


def _db_path() -> Path:
    return Path(os.getenv("USERS_DB_PATH") or ROOT / "data" / "users.db")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        username_lower TEXT PRIMARY KEY, username TEXT NOT NULL, full_name TEXT,
        salt BLOB NOT NULL, pw_hash BLOB NOT NULL, created_at REAL NOT NULL,
        failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0)""")
    return conn


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)


def create_user(username: str, password: str, full_name: str = "") -> dict:
    """Create an account. Returns {'username', 'full_name'}. Raises AuthError on bad input."""
    username, full_name = username.strip(), full_name.strip()
    if not USERNAME_RE.match(username):
        raise AuthError("Username must be 3-30 characters: letters, numbers, dot, dash or underscore.")
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")
    if len(full_name) > 60:
        raise AuthError("Name is too long (max 60 characters).")
    salt = secrets.token_bytes(16)
    try:
        with closing(_connect()) as conn, conn:
            conn.execute("INSERT INTO users (username_lower, username, full_name, salt, pw_hash, created_at) "
                         "VALUES (?,?,?,?,?,?)",
                         (username.lower(), username, full_name, salt, _hash(password, salt), time.time()))
    except sqlite3.IntegrityError:
        raise AuthError("That username is already taken. Please choose another.") from None
    return {"username": username, "full_name": full_name or username}


def authenticate(username: str, password: str) -> dict:
    """Check credentials. Raises AuthError (generic message) on failure or lockout."""
    generic = AuthError("Incorrect username or password.")
    with closing(_connect()) as conn, conn:
        row = conn.execute("SELECT * FROM users WHERE username_lower=?", (username.strip().lower(),)).fetchone()
        if row is None:
            _hash(password, b"0" * 16)  # keep timing similar for unknown users
            raise generic
        if row["locked_until"] > time.time():
            wait = int(row["locked_until"] - time.time()) + 1
            raise AuthError(f"Too many failed attempts. Try again in {wait} seconds.")
        if hmac.compare_digest(_hash(password, row["salt"]), row["pw_hash"]):
            conn.execute("UPDATE users SET failed=0, locked_until=0 WHERE username_lower=?", (row["username_lower"],))
            return {"username": row["username"], "full_name": row["full_name"] or row["username"]}
        failed = row["failed"] + 1
        locked = time.time() + LOCK_SECONDS if failed >= MAX_FAILS else 0
        conn.execute("UPDATE users SET failed=?, locked_until=? WHERE username_lower=?",
                     (0 if locked else failed, locked, row["username_lower"]))
    raise generic
