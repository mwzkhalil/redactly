"""Session identity.

Identity is derived from an HttpOnly cookie, never from a tool argument. An agent
cannot name a session it does not already hold, so every field lookup is
implicitly scoped to the caller.
"""

from __future__ import annotations

import sqlite3
import time

from ..config import get_settings
from ..domain.enums import SessionStatus
from . import crypto


def create_session(handle: sqlite3.Connection) -> tuple[str, int]:
    settings = get_settings()
    token, digest = crypto.session_token()
    now = time.time()
    cursor = handle.execute(
        """
        INSERT INTO sessions (token_hmac, status, created_at, expires_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        RETURNING id
        """,
        (digest, SessionStatus.ACTIVE, now, now + settings.session_ttl_seconds, now),
    )
    return token, int(cursor.fetchone()["id"])


def resolve_session(handle: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    now = time.time()
    row = handle.execute(
        """
        SELECT * FROM sessions
        WHERE token_hmac = ? AND status = ? AND expires_at > ?
        """,
        (crypto.session_token_digest(token), SessionStatus.ACTIVE, now),
    ).fetchone()
    if row is None:
        return None
    handle.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
    return row
