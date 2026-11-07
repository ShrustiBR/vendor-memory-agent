"""Tests for app/auth.py. Run: pytest -q"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["USERS_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "users.db")

from app import auth  # noqa: E402


def _raises(fn, *a):
    try:
        fn(*a)
    except auth.AuthError as e:
        return str(e)
    raise AssertionError("expected AuthError")


def test_signup_and_login():
    auth.create_user("Shrusti_1", "strongpass1", "Shrusti Rao")
    assert auth.authenticate("shrusti_1", "strongpass1")["full_name"] == "Shrusti Rao"  # case-insensitive


def test_duplicate_and_validation():
    auth.create_user("dupuser", "strongpass1")
    assert "taken" in _raises(auth.create_user, "DUPUSER", "strongpass1")
    assert "8 characters" in _raises(auth.create_user, "newuser", "short")
    assert "3-30" in _raises(auth.create_user, "a b", "strongpass1")


def test_wrong_password_and_lockout():
    auth.create_user("locky", "strongpass1")
    assert "Incorrect" in _raises(auth.authenticate, "locky", "nope")
    assert "Incorrect" in _raises(auth.authenticate, "nobody", "whatever")
    for _ in range(auth.MAX_FAILS):
        _raises(auth.authenticate, "locky", "bad")
    assert "Too many" in _raises(auth.authenticate, "locky", "strongpass1")


def test_password_not_stored_plain():
    import sqlite3
    raw = sqlite3.connect(os.environ["USERS_DB_PATH"]).execute("SELECT pw_hash FROM users").fetchall()
    assert all(b"strongpass1" not in r[0] for r in raw)
