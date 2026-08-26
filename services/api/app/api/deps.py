"""Request-scoped dependencies."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from ..config import get_settings
from ..db import session as db
from ..domain.enums import ToolStatus
from ..security import sessions
from ..services import redaction


@dataclass(frozen=True, slots=True)
class SessionContext:
    id: int


async def current_session(request: Request) -> SessionContext:
    """Derive the caller's identity from the cookie, never from the body.

    An agent can put anything in a tool argument, so accepting a session or
    document id from one would let it address another browser's view.
    """
    token = request.cookies.get(get_settings().cookie_name)
    row = await db.run(sessions.resolve_session, token)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": str(ToolStatus.UNAVAILABLE), "reason": "no_active_session"},
        )
    return SessionContext(id=int(row["id"]))


def _load_session_document(handle: sqlite3.Connection, session_id: int, slug: str) -> sqlite3.Row | None:
    existing = redaction.get_session_document(handle, session_id=session_id, slug=slug)
    if existing is not None:
        return existing
    if redaction.open_document(handle, session_id=session_id, slug=slug) is None:
        return None
    return redaction.get_session_document(handle, session_id=session_id, slug=slug)


async def require_session_document(session_id: int, slug: str) -> sqlite3.Row:
    row = await db.run(_load_session_document, session_id, slug)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": str(ToolStatus.NOT_FOUND), "reason": "document_unavailable"},
        )
    return row


def _load_field(handle: sqlite3.Connection, session_id: int, field_ref: str) -> sqlite3.Row | None:
    return redaction.get_session_field(handle, session_id=session_id, field_ref=field_ref)


async def require_field(session_id: int, field_ref: str, session_document_id: int) -> sqlite3.Row:
    row = await db.run(_load_field, session_id, field_ref)
    # An unknown reference and a reference belonging to another document are the
    # same answer on purpose; distinguishing them would confirm existence.
    if row is None or int(row["session_document_id"]) != session_document_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": str(ToolStatus.NOT_FOUND), "reason": "unknown_field_reference"},
        )
    return row
