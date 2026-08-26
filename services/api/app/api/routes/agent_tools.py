"""The five WebMCP tool endpoints.

Each route mirrors one registered tool. They return small discriminated results
and never a backend exception message: a stack trace or SQL error is both an
information leak and something an agent may quote verbatim into a summary.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends, Path, Request

from ...config import get_settings
from ...db import session as db
from ...db.session import immediate
from ...domain.enums import Actor, EventType, RequestKind, RequestStatus, ToolStatus
from ...schemas import (
    SLUG_PATTERN,
    AuditRequest,
    ChallengeRequest,
    UnmaskRequest,
    VerifyRequest,
    ViewRequest,
)
from ...services import approvals, audit, challenges, cursors, redaction, verification, view
from ..deps import SessionContext, current_session, require_field, require_session_document

router = APIRouter(prefix="/agent/documents/{slug}", tags=["agent-tools"])

POLL_INTERVAL_SECONDS = 0.25
DISCONNECT_GRACE_SECONDS = 2.0


@router.post("/view")
async def request_document_view(
    body: ViewRequest,
    slug: str = Path(pattern=SLUG_PATTERN),
    session: SessionContext = Depends(current_session),
) -> dict[str, Any]:
    session_document = await require_session_document(session.id, slug)
    result = await db.run(view.build_view, session_document=session_document, cursor=body.cursor)
    if result["status"] == ToolStatus.OK:
        await db.run(
            redaction.record_view_event,
            session_id=session.id,
            session_document_id=int(session_document["id"]),
            purpose=body.purpose,
            page_index=int(result["page_index"]),
            has_more=bool(result["has_more"]),
            masked_count=int(result["masked_field_count"]),
            visible_count=int(result["visible_field_count"]),
            view_revision=int(result["view_revision"]),
        )
    return result


@router.post("/verify")
async def verify_without_reveal(
    body: VerifyRequest,
    slug: str = Path(pattern=SLUG_PATTERN),
    session: SessionContext = Depends(current_session),
) -> dict[str, Any]:
    session_document = await require_session_document(session.id, slug)
    field = await require_field(session.id, body.field_ref, int(session_document["id"]))
    return await db.run(
        verification.verify,
        session_id=session.id,
        field=field,
        comparison_value=body.comparison_value,
        purpose=body.purpose,
    )


async def _await_decision(
    http_request: Request,
    *,
    session_id: int,
    request_ref: str,
    deadline: float,
) -> sqlite3.Row | None:
    """Block until a human decides, the deadline passes, or the caller vanishes.

    A dropped connection is treated as an abort and cancels the request. Leaving
    it pending would keep a modal on the owner's screen for a tool call whose
    result nobody can receive.
    """
    while True:
        row = await db.run(approvals.poll_request, session_id=session_id, request_ref=request_ref)
        if row is None:
            return None
        if row["status"] != RequestStatus.PENDING:
            return row
        if await http_request.is_disconnected():
            await db.run(approvals.cancel_request, session_id=session_id, request_ref=request_ref)
            return None
        if time.time() > deadline + DISCONNECT_GRACE_SECONDS:
            # The sweep should already have flipped this; cancel so the request
            # cannot outlive the tool call that created it.
            await db.run(approvals.cancel_request, session_id=session_id, request_ref=request_ref)
            return None
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


@router.post("/unmask")
async def request_field_unmask(
    body: UnmaskRequest,
    http_request: Request,
    slug: str = Path(pattern=SLUG_PATTERN),
    session: SessionContext = Depends(current_session),
) -> dict[str, Any]:
    session_document = await require_session_document(session.id, slug)
    field = await require_field(session.id, body.field_ref, int(session_document["id"]))

    created = await db.run(
        approvals.create_request,
        session_id=session.id,
        field=field,
        kind=RequestKind.UNMASK,
        reason=body.reason,
    )
    if created["status"] != "pending":
        return created

    resolved = await _await_decision(
        http_request,
        session_id=session.id,
        request_ref=created["request_ref"],
        deadline=float(created["expires_at"]),
    )
    if resolved is None:
        return {"status": str(ToolStatus.CANCELLED), "request_ref": created["request_ref"]}
    if resolved["status"] != RequestStatus.APPROVED:
        return {
            "status": approvals.terminal_tool_status(resolved["status"]),
            "request_ref": created["request_ref"],
        }

    result = await db.run(
        approvals.consume_grant, session_id=session.id, request_ref=created["request_ref"]
    )
    result["request_ref"] = created["request_ref"]
    return result


@router.post("/unmask/{request_ref}/retrieve")
async def retrieve_reveal(
    request_ref: str,
    slug: str = Path(pattern=SLUG_PATTERN),
    session: SessionContext = Depends(current_session),
) -> dict[str, Any]:
    """Second retrieval attempt for an approved request.

    This exists to be demonstrated rather than used: the same approval never
    yields the value twice, so a replay returns `already_consumed` next to the
    receipt id that proves the first delivery happened.
    """
    await require_session_document(session.id, slug)
    return await db.run(approvals.consume_grant, session_id=session.id, request_ref=request_ref)


@router.post("/challenge")
async def challenge_redaction(
    body: ChallengeRequest,
    http_request: Request,
    slug: str = Path(pattern=SLUG_PATTERN),
    session: SessionContext = Depends(current_session),
) -> dict[str, Any]:
    session_document = await require_session_document(session.id, slug)
    field = await require_field(session.id, body.field_ref, int(session_document["id"]))
    assessment = challenges.model_assessment(
        base_entity_type=field["base_entity_type"],
        category=field["category"],
        confidence=float(field["confidence"]),
    )

    created = await db.run(
        approvals.create_request,
        session_id=session.id,
        field=field,
        kind=RequestKind.CHALLENGE,
        reason=body.reasoning,
    )
    if created["status"] != "pending":
        return created | {"model_assessment": assessment}

    resolved = await _await_decision(
        http_request,
        session_id=session.id,
        request_ref=created["request_ref"],
        deadline=float(created["expires_at"]),
    )
    if resolved is None:
        return {"status": str(ToolStatus.CANCELLED), "model_assessment": assessment}
    if resolved["status"] == RequestStatus.APPROVED:
        return {
            "status": str(ToolStatus.APPROVED),
            "view_revision": int(resolved["view_revision"]),
            "model_assessment": assessment,
        }
    if resolved["status"] == RequestStatus.DENIED:
        return {"status": str(ToolStatus.REJECTED), "model_assessment": assessment}
    return {
        "status": approvals.terminal_tool_status(resolved["status"]),
        "model_assessment": assessment,
    }


def _read_audit(
    handle: sqlite3.Connection,
    *,
    session_id: int,
    session_document_id: int,
    cursor: str | None,
    limit: int,
    purpose: str,
) -> dict[str, Any]:
    after_id = None
    if cursor:
        payload = cursors.decode(cursor)
        if not payload or payload.get("d") != session_document_id:
            return {"status": str(ToolStatus.STALE), "reason": "invalid_cursor"}
        after_id = int(payload.get("a", 0))

    events, next_after = audit.read_page(
        handle,
        session_document_id=session_document_id,
        after_id=after_id,
        limit=limit,
    )
    # Logged after the page is built so a read never appears in its own results.
    with immediate(handle):
        audit.record(
            handle,
            actor=Actor.AGENT,
            event_type=EventType.AUDIT_LOG_READ,
            outcome="ok",
            session_id=session_id,
            session_document_id=session_document_id,
            reason=purpose,
            metadata={"limit": limit, "returned": len(events), "has_more": next_after is not None},
        )
    return {
        "status": str(ToolStatus.OK),
        "events": events,
        "has_more": next_after is not None,
        "next_cursor": (
            cursors.encode({"d": session_document_id, "a": next_after}) if next_after else None
        ),
    }


@router.post("/audit")
async def get_redaction_audit_log(
    body: AuditRequest,
    slug: str = Path(pattern=SLUG_PATTERN),
    session: SessionContext = Depends(current_session),
) -> dict[str, Any]:
    session_document = await require_session_document(session.id, slug)
    settings = get_settings()
    return await db.run(
        _read_audit,
        session_id=session.id,
        session_document_id=int(session_document["id"]),
        cursor=body.cursor,
        limit=min(body.limit, settings.audit_max_limit),
        purpose=body.purpose,
    )
