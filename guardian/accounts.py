"""Per-user accounts — registration, login sessions, and device tokens.

Lets N developers each connect their own opencode instance to one deployed
Guardian. Every account owns exactly the instances and reasoning sessions that
registered with its device token, so the dashboard shows a user only their own
devices — never everyone's.

Stored in SQLite (stdlib only). Passwords are hashed with PBKDF2-HMAC + a per-
user salt. Device tokens are opaque secrets a teammate pastes into their env
(GUARDIAN_DEVICE_TOKEN) so Guardian can attribute their plugin to them.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from datetime import UTC, datetime

PBKDF2_ITERATIONS = 200_000
SESSION_TTL_S = 60 * 60 * 24 * 7  # 7 days


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _hash_password(password: str, salt: bytes | None = None) -> str:
    """PBKDF2-HMAC-SHA256, stored as `salt_hex$hash_hex`."""
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except Exception:
        return False
    expected = _hash_password(password, salt)
    return hmac.compare_digest(expected, stored)


class UserStore:
    """SQLite-backed users + sessions.

    Tables:
      users    (id, email UNIQUE, password_hash, device_token UNIQUE, is_admin, created_at)
      sessions (token PK, user_id, created_at, expires_at)
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        device_token TEXT NOT NULL UNIQUE,
                        is_admin INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        token TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at REAL NOT NULL
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # --- users ----------------------------------------------------------------
    def register(self, email: str, password: str) -> dict:
        """Create an account. The FIRST account ever created is the admin."""
        email = (email or "").strip().lower()
        if not email or "@" not in email or len(email) > 200:
            raise ValueError("a valid email is required")
        if not password or len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        with self._lock:
            conn = self._conn()
            try:
                count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
                is_admin = 1 if count == 0 else 0
                user = {
                    "email": email,
                    "password_hash": _hash_password(password),
                    "device_token": secrets.token_urlsafe(32),
                    "is_admin": is_admin,
                    "created_at": _now(),
                }
                try:
                    cur = conn.execute(
                        "INSERT INTO users (email, password_hash, device_token, is_admin, created_at) "
                        "VALUES (:email, :password_hash, :device_token, :is_admin, :created_at)",
                        user,
                    )
                    conn.commit()
                except sqlite3.IntegrityError:
                    raise ValueError("an account with that email already exists") from None
                return self._public_user({**user, "id": cur.lastrowid})
            finally:
                conn.close()

    def login(self, email: str, password: str) -> dict:
        """Authenticate and mint a session token."""
        email = (email or "").strip().lower()
        with self._lock:
            user = self.user_by_email(email)
            if not user or not _verify_password(password or "", user["password_hash"]):
                raise ValueError("invalid email or password")
            token = secrets.token_urlsafe(32)
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                    (token, user["id"], _now(), time.time() + SESSION_TTL_S),
                )
                conn.commit()
            finally:
                conn.close()
            return {"token": token, "user": self._public_user(user)}

    def logout(self, token: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
            finally:
                conn.close()

    def user_by_email(self, email: str) -> dict | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def user_by_id(self, user_id: int) -> dict | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def user_for_session(self, token: str) -> dict | None:
        """Resolve a session token to its user, or None if missing/expired."""
        if not token:
            return None
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM sessions WHERE token = ?", (token,)
                ).fetchone()
            finally:
                conn.close()
        if not row or time.time() >= row["expires_at"]:
            return None
        return self.user_by_id(row["user_id"])

    def user_for_device_token(self, device_token: str) -> dict | None:
        """Resolve a plugin's GUARDIAN_DEVICE_TOKEN to its owning user."""
        if not device_token:
            return None
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM users WHERE device_token = ?", (device_token,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def reset(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript("DELETE FROM sessions; DELETE FROM users;")
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _public_user(user: dict) -> dict:
        return {
            "id": user["id"],
            "email": user["email"],
            "device_token": user["device_token"],
            "is_admin": bool(user["is_admin"]),
            "created_at": user["created_at"],
        }
