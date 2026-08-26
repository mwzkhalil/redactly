"""Human-facing document routes.

The human viewer receives the same masked text an agent would, unpaginated. It
does not receive raw values, so the DOM never holds one and a browser-resident
agent reading the page learns nothing the tools would not already tell it.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Path, Query

from ...db import session as db
from ...domain.enums import Actor, EventType
from ...db.session import immediate
from ...schemas import SLUG_PATTERN
from ...services import audit, redaction, view
from ..deps import SessionContext, current_session, require_session_document

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("")
async def list_documents(session: SessionContext = Depends(current_session)) -> dict[str, object]:
    documents = await db.run(redaction.list_documents)
    return {"documents": documents}


def _masked_document(handle: sqlite3.Connection, session_id: int, session_document_id: int) -> dict[str, object]:
    session_document = handle.execute(
        """
        SELECT session_documents.*, documents.slug, documents.title
        FROM session_documents
        JOIN documents ON documents.id = session_documents.document_id
        WHERE session_documents.id = ?
        """,
        (session_document_id,),
    ).fetchone()

    text = redaction.document_text(handle, session_document_id=session_document_id)
    fields = redaction.list_fields(handle, session_document_id=session_document_id)
    tokens = view.build_tokens(text, fields)
    masked_text = "".join(token.text for token in tokens)

    with immediate(handle):
        audit.record(
            handle,
            actor=Actor.HUMAN,
            event_type=EventType.DOCUMENT_VIEWED,
            outcome="ok",
            session_id=session_id,
            session_document_id=session_document_id,
            reason="owner opened the document viewer",
            metadata={
                "view_revision": int(session_document["view_revision"]),
                "masked_field_count": sum(1 for item in fields if item.masked),
                "visible_field_count": sum(1 for item in fields if not item.masked),
            },
        )

    return {
        "slug": session_document["slug"],
        "title": session_document["title"],
        "view_revision": int(session_document["view_revision"]),
        "masked_text": masked_text,
        "fields": [view.describe_field(item) for item in fields],
    }


@router.get("/{slug}/masked")
async def masked_document(
    slug: str = Path(pattern=SLUG_PATTERN),
    session: SessionContext = Depends(current_session),
) -> dict[str, object]:
    session_document = await require_session_document(session.id, slug)
    return await db.run(_masked_document, session.id, int(session_document["id"]))


def _owner_audit(handle: sqlite3.Connection, session_document_id: int, limit: int) -> dict[str, object]:
    events, next_after = audit.read_page(
        handle,
        session_document_id=session_document_id,
        after_id=None,
        limit=limit,
    )
    return {"events": events, "has_more": next_after is not None}


@router.get("/{slug}/audit")
async def owner_audit(
    slug: str = Path(pattern=SLUG_PATTERN),
    limit: int = Query(default=50, ge=1, le=200),
    session: SessionContext = Depends(current_session),
) -> dict[str, object]:
    """Owner's read of the trail.

    Unlike the agent's `get_redaction_audit_log`, this does not append an event.
    The panel refreshes on a timer, and a log that grows because it is being
    watched is a log nobody can read.
    """
    session_document = await require_session_document(session.id, slug)
    return await db.run(_owner_audit, int(session_document["id"]), limit)
