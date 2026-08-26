"""Append-only audit trail.

`record` never opens its own transaction. It is called from inside the caller's
`BEGIN IMMEDIATE` block so the state change and its audit row commit together —
if the insert raises, the mutation rolls back with it and there is no window in
which an action succeeded unlogged.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from ..config import get_settings
from ..domain import policies
from ..domain.enums import Actor, EventType


def record(
    handle: sqlite3.Connection,
    *,
    actor: Actor,
    event_type: EventType,
    outcome: str,
    session_id: int | None = None,
    session_document_id: int | None = None,
    session_field_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    occurred_at: float | None = None,
) -> int:
    cursor = handle.execute(
        """
        INSERT INTO audit_events (
            uuid, session_id, session_document_id, session_field_id,
            actor, event_type, outcome, reason, metadata, occurred_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            str(uuid.uuid4()),
            session_id,
            session_document_id,
            session_field_id,
            str(actor),
            str(event_type),
            outcome,
            policies.sanitise_free_text(reason) or None,
            json.dumps(policies.filter_metadata(metadata), ensure_ascii=False),
            occurred_at if occurred_at is not None else time.time(),
        ),
    )
    return int(cursor.fetchone()["id"])


def read_page(
    handle: sqlite3.Connection,
    *,
    session_document_id: int,
    after_id: int | None,
    limit: int,
) -> tuple[list[dict[str, Any]], int | None]:
    settings = get_settings()
    bounded = max(1, min(limit, settings.audit_max_limit))
    rows = handle.execute(
        """
        SELECT audit_events.id,
               audit_events.uuid,
               audit_events.actor,
               audit_events.event_type,
               audit_events.outcome,
               audit_events.reason,
               audit_events.metadata,
               audit_events.occurred_at,
               session_fields.public_ref AS field_ref
        FROM audit_events
        LEFT JOIN session_fields ON session_fields.id = audit_events.session_field_id
        WHERE audit_events.session_document_id = ?
          AND audit_events.id > ?
        ORDER BY audit_events.id ASC
        LIMIT ?
        """,
        (session_document_id, after_id or 0, bounded + 1),
    ).fetchall()

    has_more = len(rows) > bounded
    page = rows[:bounded]
    events = [
        {
            "event_id": row["uuid"],
            "actor": row["actor"],
            "event_type": row["event_type"],
            "outcome": row["outcome"],
            "reason": row["reason"],
            "field_ref": row["field_ref"],
            "metadata": json.loads(row["metadata"]),
            "occurred_at": row["occurred_at"],
        }
        for row in page
    ]
    next_after = int(page[-1]["id"]) if page and has_more else None
    return events, next_after
