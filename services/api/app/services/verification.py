"""`verify_without_reveal`.

This is the most dangerous tool in the set precisely because it looks harmless.
An unbounded equality check against a hidden value is a brute-force oracle: an
agent can enumerate a nine-digit identifier if it is allowed to ask often enough.
Three things keep it honest — a per-field and per-session rate limit, a
constant-time digest comparison, and never persisting the guess.
"""

from __future__ import annotations

import sqlite3
import time

from ..config import get_settings
from ..db.session import immediate
from ..domain import policies
from ..domain.enums import Actor, EventType, Override, ToolStatus
from ..security import crypto
from ..security.canonical import canonicalise
from . import audit


def _attempts_in_window(handle: sqlite3.Connection, *, session_field_id: int, since: float) -> int:
    row = handle.execute(
        "SELECT COUNT(*) AS total FROM verification_attempts WHERE session_field_id = ? AND created_at >= ?",
        (session_field_id, since),
    ).fetchone()
    return int(row["total"])


def _session_attempts_in_window(handle: sqlite3.Connection, *, session_id: int, since: float) -> int:
    row = handle.execute(
        """
        SELECT COUNT(*) AS total
        FROM verification_attempts
        JOIN session_fields ON session_fields.id = verification_attempts.session_field_id
        JOIN session_documents ON session_documents.id = session_fields.session_document_id
        WHERE session_documents.session_id = ? AND verification_attempts.created_at >= ?
        """,
        (session_id, since),
    ).fetchone()
    return int(row["total"])


def verify(
    handle: sqlite3.Connection,
    *,
    session_id: int,
    field: sqlite3.Row,
    comparison_value: str,
    purpose: str,
) -> dict[str, object]:
    settings = get_settings()
    now = time.time()
    window_start = now - settings.verify_window_seconds
    session_document_id = int(field["session_document_id"])
    session_field_id = int(field["session_field_id"])

    if field["override"] == Override.NON_SENSITIVE:
        return {
            "status": str(ToolStatus.NOT_SENSITIVE),
            "detail": "field is no longer masked in this session; read it from the document view",
        }

    with immediate(handle):
        field_attempts = _attempts_in_window(handle, session_field_id=session_field_id, since=window_start)
        session_attempts = _session_attempts_in_window(handle, session_id=session_id, since=window_start)
        over_field_limit = field_attempts >= settings.verify_max_per_field
        over_session_limit = session_attempts >= settings.verify_max_per_session

        if over_field_limit or over_session_limit:
            retry_after = int(settings.verify_window_seconds)
            audit.record(
                handle,
                actor=Actor.AGENT,
                event_type=EventType.VERIFICATION_RATE_LIMITED,
                outcome=str(ToolStatus.RATE_LIMITED),
                session_id=session_id,
                session_document_id=session_document_id,
                session_field_id=session_field_id,
                reason=purpose,
                metadata={
                    "category": field["category"],
                    "attempts_in_window": field_attempts,
                    "retry_after_seconds": retry_after,
                },
            )
            return {
                "status": str(ToolStatus.RATE_LIMITED),
                "retry_after_seconds": retry_after,
                "scope": "field" if over_field_limit else "session",
            }

        separator = crypto.field_domain_separator(
            field["document_slug"],
            field["content_sha256"],
            int(field["start_offset"]),
            int(field["end_offset"]),
        )
        guess_digest = crypto.canonical_digest(
            canonicalise(comparison_value, field["category"]), separator
        )
        matches = crypto.digests_equal(guess_digest, bytes(field["canonical_hmac"]))

        # The guess itself is never a column. Only the outcome is, which is all
        # the rate limiter and the audit trail need.
        handle.execute(
            "INSERT INTO verification_attempts (session_field_id, matches, purpose, created_at) VALUES (?, ?, ?, ?)",
            (session_field_id, 1 if matches else 0, policies.sanitise_free_text(purpose), now),
        )
        audit.record(
            handle,
            actor=Actor.AGENT,
            event_type=EventType.FIELD_VERIFIED,
            outcome="match" if matches else "no_match",
            session_id=session_id,
            session_document_id=session_document_id,
            session_field_id=session_field_id,
            reason=purpose,
            metadata={
                "category": field["category"],
                "matches": matches,
                "attempts_in_window": field_attempts + 1,
            },
            occurred_at=now,
        )

    return {
        "status": str(ToolStatus.CHECKED),
        "matches": matches,
        "category": field["category"],
        "attempts_remaining": max(0, settings.verify_max_per_field - (field_attempts + 1)),
    }
