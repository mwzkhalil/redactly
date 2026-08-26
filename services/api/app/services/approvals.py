"""Approval requests and at-most-once reveal grants.

The one invariant everything here exists to protect: a value is returned to a
tool call at most once. Revocation after the fact is meaningless — once a value
is in an agent's context it may already be in a model provider's logs, a
downstream tool call, or a summary the agent wrote — so the only enforceable
guarantee is that the second caller never gets it.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from ..config import get_settings
from ..db.session import immediate
from ..domain import policies
from ..domain.enums import (
    REQUEST_STATUS_TO_TOOL_STATUS,
    Actor,
    EventType,
    GrantState,
    Override,
    RequestKind,
    RequestStatus,
    ToolStatus,
)
from ..security import crypto
from . import audit, challenges, redaction

_RESOLUTION_EVENT = {
    RequestKind.UNMASK: EventType.UNMASK_RESOLVED,
    RequestKind.CHALLENGE: EventType.CHALLENGE_RESOLVED,
}
_REQUEST_EVENT = {
    RequestKind.UNMASK: EventType.UNMASK_REQUESTED,
    RequestKind.CHALLENGE: EventType.CHALLENGE_REQUESTED,
}


def sweep_expired(handle: sqlite3.Connection, *, now: float | None = None) -> None:
    """Public wrapper for the deadline sweep.

    A waiting tool call polls several times a second, so the write transaction is
    only opened when there is actually something past its deadline. Taking the
    reserved lock on every poll would make the owner's own decision contend with
    the request it is deciding.
    """
    moment = now if now is not None else time.time()
    if not _needs_sweep(handle, moment):
        return
    with immediate(handle):
        _sweep(handle, moment)


def _needs_sweep(handle: sqlite3.Connection, now: float) -> bool:
    row = handle.execute(
        """
        SELECT
            EXISTS (
                SELECT 1 FROM approval_requests WHERE status = ? AND expires_at <= ?
            ) AS stale_requests,
            EXISTS (
                SELECT 1 FROM reveal_grants WHERE state = ? AND expires_at <= ?
            ) AS stale_grants
        """,
        (str(RequestStatus.PENDING), now, str(GrantState.READY), now),
    ).fetchone()
    return bool(row["stale_requests"]) or bool(row["stale_grants"])


def _sweep(handle: sqlite3.Connection, now: float) -> None:
    """Retire anything past its deadline. Caller holds the transaction.

    Deadlines are enforced on read rather than by a background worker: a request
    that nobody looks at cannot leak, and a request that is looked at is always
    evaluated against the current clock.
    """
    for row in handle.execute(
        """
        SELECT approval_requests.id,
               approval_requests.kind,
               approval_requests.session_document_id,
               approval_requests.session_field_id,
               session_documents.session_id
        FROM approval_requests
        JOIN session_documents ON session_documents.id = approval_requests.session_document_id
        WHERE approval_requests.status = ? AND approval_requests.expires_at <= ?
        """,
        (str(RequestStatus.PENDING), now),
    ).fetchall():
        handle.execute(
            "UPDATE approval_requests SET status = ?, resolved_at = ? WHERE id = ?",
            (str(RequestStatus.TIMED_OUT), now, row["id"]),
        )
        audit.record(
            handle,
            actor=Actor.SYSTEM,
            event_type=_RESOLUTION_EVENT[RequestKind(row["kind"])],
            outcome=str(ToolStatus.TIMED_OUT),
            session_id=row["session_id"],
            session_document_id=row["session_document_id"],
            session_field_id=row["session_field_id"],
            metadata={"request_kind": row["kind"]},
            occurred_at=now,
        )

    for row in handle.execute(
        """
        SELECT reveal_grants.id,
               reveal_grants.session_field_id,
               approval_requests.session_document_id,
               session_documents.session_id
        FROM reveal_grants
        JOIN approval_requests ON approval_requests.id = reveal_grants.request_id
        JOIN session_documents ON session_documents.id = approval_requests.session_document_id
        WHERE reveal_grants.state = ? AND reveal_grants.expires_at <= ?
        """,
        (str(GrantState.READY), now),
    ).fetchall():
        handle.execute(
            "UPDATE reveal_grants SET state = ? WHERE id = ?",
            (str(GrantState.EXPIRED), row["id"]),
        )
        audit.record(
            handle,
            actor=Actor.SYSTEM,
            event_type=EventType.UNMASK_RESOLVED,
            outcome="grant_expired",
            session_id=row["session_id"],
            session_document_id=row["session_document_id"],
            session_field_id=row["session_field_id"],
            metadata={"grant_state": str(GrantState.EXPIRED)},
            occurred_at=now,
        )


def create_request(
    handle: sqlite3.Connection,
    *,
    session_id: int,
    field: sqlite3.Row,
    kind: RequestKind,
    reason: str,
) -> dict[str, Any]:
    settings = get_settings()
    now = time.time()
    session_document_id = int(field["session_document_id"])
    session_field_id = int(field["session_field_id"])

    with immediate(handle):
        _sweep(handle, now)

        current = handle.execute(
            "SELECT override, version FROM session_fields WHERE id = ?",
            (session_field_id,),
        ).fetchone()
        if current["override"] == Override.NON_SENSITIVE:
            return {
                "status": str(ToolStatus.NOT_SENSITIVE),
                "detail": "field is already visible in this session's document view",
            }

        # One pending request per field per kind. Otherwise a loop in an agent
        # turns the owner's approval queue into a denial-of-service surface.
        for stale in handle.execute(
            """
            SELECT id FROM approval_requests
            WHERE session_field_id = ? AND kind = ? AND status = ?
            """,
            (session_field_id, str(kind), str(RequestStatus.PENDING)),
        ).fetchall():
            handle.execute(
                "UPDATE approval_requests SET status = ?, resolved_at = ? WHERE id = ?",
                (str(RequestStatus.CANCELLED), now, stale["id"]),
            )
            audit.record(
                handle,
                actor=Actor.SYSTEM,
                event_type=_RESOLUTION_EVENT[kind],
                outcome=str(ToolStatus.CANCELLED),
                session_id=session_id,
                session_document_id=session_document_id,
                session_field_id=session_field_id,
                reason="superseded by a newer request for the same field",
                metadata={"request_kind": str(kind)},
                occurred_at=now,
            )

        request_ref = crypto.public_ref("req")
        expires_at = now + settings.approval_timeout_seconds
        handle.execute(
            """
            INSERT INTO approval_requests (
                public_ref, kind, session_document_id, session_field_id,
                status, reason, field_version, requested_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_ref,
                str(kind),
                session_document_id,
                session_field_id,
                str(RequestStatus.PENDING),
                policies.sanitise_free_text(reason),
                int(current["version"]),
                now,
                expires_at,
            ),
        )
        audit.record(
            handle,
            actor=Actor.AGENT,
            event_type=_REQUEST_EVENT[kind],
            outcome=str(RequestStatus.PENDING),
            session_id=session_id,
            session_document_id=session_document_id,
            session_field_id=session_field_id,
            reason=reason,
            metadata={
                "request_kind": str(kind),
                "category": field["category"],
                "field_version": int(current["version"]),
            },
            occurred_at=now,
        )

    return {
        "status": str(RequestStatus.PENDING).lower(),
        "request_ref": request_ref,
        "expires_at": expires_at,
        "timeout_seconds": settings.approval_timeout_seconds,
    }


def get_request(handle: sqlite3.Connection, *, session_id: int, request_ref: str) -> sqlite3.Row | None:
    return handle.execute(
        """
        SELECT approval_requests.*,
               session_documents.session_id,
               session_documents.view_revision,
               session_fields.version AS current_field_version,
               session_fields.public_ref AS field_ref,
               redaction_fields.category,
               redaction_fields.base_entity_type,
               redaction_fields.confidence
        FROM approval_requests
        JOIN session_documents ON session_documents.id = approval_requests.session_document_id
        JOIN session_fields ON session_fields.id = approval_requests.session_field_id
        JOIN redaction_fields ON redaction_fields.id = session_fields.redaction_field_id
        WHERE approval_requests.public_ref = ? AND session_documents.session_id = ?
        """,
        (request_ref, session_id),
    ).fetchone()


def poll_request(handle: sqlite3.Connection, *, session_id: int, request_ref: str) -> sqlite3.Row | None:
    """Sweep deadlines, then read the request. Used by the waiting tool routes."""
    sweep_expired(handle)
    return get_request(handle, session_id=session_id, request_ref=request_ref)


def pending_signature(handle: sqlite3.Connection, *, session_id: int) -> str:
    """Cheap change token for the owner queue, so the UI can long-poll."""
    row = handle.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(MAX(approval_requests.id), 0) AS newest,
               COALESCE(MAX(session_documents.view_revision), 0) AS revision
        FROM session_documents
        LEFT JOIN approval_requests
               ON approval_requests.session_document_id = session_documents.id
              AND approval_requests.status = ?
        WHERE session_documents.session_id = ?
        """,
        (str(RequestStatus.PENDING), session_id),
    ).fetchone()
    return f"{row['total']}:{row['newest']}:{row['revision']}"


def list_pending(handle: sqlite3.Connection, *, session_id: int) -> list[dict[str, Any]]:
    """Owner-facing queue.

    Note what is absent: the field's value, and any surrounding document text.
    The approval modal renders on the same page an agent can inspect, so the
    human decides from category, structural hint, and the agent's stated reason.
    """
    sweep_expired(handle)
    rows = handle.execute(
        """
        SELECT approval_requests.public_ref,
               approval_requests.kind,
               approval_requests.reason,
               approval_requests.requested_at,
               approval_requests.expires_at,
               session_fields.public_ref AS field_ref,
               redaction_fields.category,
               redaction_fields.base_entity_type,
               redaction_fields.confidence,
               redaction_fields.safe_context_hint,
               documents.slug AS document_slug,
               documents.title AS document_title
        FROM approval_requests
        JOIN session_documents ON session_documents.id = approval_requests.session_document_id
        JOIN documents ON documents.id = session_documents.document_id
        JOIN session_fields ON session_fields.id = approval_requests.session_field_id
        JOIN redaction_fields ON redaction_fields.id = session_fields.redaction_field_id
        WHERE session_documents.session_id = ? AND approval_requests.status = ?
        ORDER BY approval_requests.requested_at ASC
        """,
        (session_id, str(RequestStatus.PENDING)),
    ).fetchall()

    return [
        {
            "request_ref": row["public_ref"],
            "kind": row["kind"],
            "reason": row["reason"],
            "requested_at": row["requested_at"],
            "expires_at": row["expires_at"],
            "field_ref": row["field_ref"],
            "category": row["category"],
            "safe_context_hint": row["safe_context_hint"],
            "document_slug": row["document_slug"],
            "document_title": row["document_title"],
            # A challenge asks the human to review a classification, so the
            # classifier's own assessment is part of the question.
            "model_assessment": (
                challenges.model_assessment(
                    base_entity_type=row["base_entity_type"],
                    category=row["category"],
                    confidence=float(row["confidence"]),
                )
                if row["kind"] == RequestKind.CHALLENGE
                else None
            ),
        }
        for row in rows
    ]


def cancel_request(handle: sqlite3.Connection, *, session_id: int, request_ref: str) -> dict[str, Any]:
    now = time.time()
    with immediate(handle):
        request = get_request(handle, session_id=session_id, request_ref=request_ref)
        if request is None:
            return {"status": str(ToolStatus.NOT_FOUND)}
        if request["status"] != RequestStatus.PENDING:
            return {"status": terminal_tool_status(request["status"])}
        handle.execute(
            "UPDATE approval_requests SET status = ?, resolved_at = ? WHERE id = ?",
            (str(RequestStatus.CANCELLED), now, request["id"]),
        )
        audit.record(
            handle,
            actor=Actor.AGENT,
            event_type=_RESOLUTION_EVENT[RequestKind(request["kind"])],
            outcome=str(ToolStatus.CANCELLED),
            session_id=session_id,
            session_document_id=request["session_document_id"],
            session_field_id=request["session_field_id"],
            reason="caller aborted",
            metadata={"request_kind": request["kind"]},
            occurred_at=now,
        )
    return {"status": str(ToolStatus.CANCELLED)}


def resolve_decision(
    handle: sqlite3.Connection,
    *,
    session_id: int,
    request_ref: str,
    approve: bool,
    note: str | None,
) -> dict[str, Any]:
    """Record the human decision. Approving an unmask only issues a grant."""
    now = time.time()
    with immediate(handle):
        _sweep(handle, now)
        request = get_request(handle, session_id=session_id, request_ref=request_ref)
        if request is None:
            return {"status": str(ToolStatus.NOT_FOUND)}
        if request["status"] != RequestStatus.PENDING:
            return {"status": "already_resolved", "request_status": request["status"]}

        kind = RequestKind(request["kind"])
        common = {
            "session_id": session_id,
            "session_document_id": int(request["session_document_id"]),
            "session_field_id": int(request["session_field_id"]),
        }

        if int(request["current_field_version"]) != int(request["field_version"]):
            handle.execute(
                "UPDATE approval_requests SET status = ?, resolved_at = ?, human_note = ? WHERE id = ?",
                (str(RequestStatus.STALE), now, policies.sanitise_free_text(note), request["id"]),
            )
            audit.record(
                handle,
                actor=Actor.SYSTEM,
                event_type=_RESOLUTION_EVENT[kind],
                outcome=str(ToolStatus.STALE),
                reason="field changed after the request was raised",
                metadata={"request_kind": str(kind)},
                occurred_at=now,
                **common,
            )
            return {"status": str(ToolStatus.STALE)}

        if not approve:
            handle.execute(
                "UPDATE approval_requests SET status = ?, resolved_at = ?, human_note = ? WHERE id = ?",
                (str(RequestStatus.DENIED), now, policies.sanitise_free_text(note), request["id"]),
            )
            audit.record(
                handle,
                actor=Actor.HUMAN,
                event_type=_RESOLUTION_EVENT[kind],
                outcome=str(ToolStatus.DENIED),
                reason=note,
                metadata={"request_kind": str(kind), "category": request["category"]},
                occurred_at=now,
                **common,
            )
            return {"status": str(ToolStatus.DENIED)}

        if kind is RequestKind.CHALLENGE:
            new_revision = challenges.apply_reclassification(
                handle,
                session_id=session_id,
                session_document_id=int(request["session_document_id"]),
                session_field_id=int(request["session_field_id"]),
                field_version=int(request["field_version"]),
                category=request["category"],
                confidence=float(request["confidence"]),
            )
            if new_revision is None:
                return {"status": str(ToolStatus.STALE)}
            # apply_reclassification marks every pending request on the field
            # STALE, including this one, so approval is written afterwards.
            handle.execute(
                "UPDATE approval_requests SET status = ?, resolved_at = ?, human_note = ? WHERE id = ?",
                (str(RequestStatus.APPROVED), now, policies.sanitise_free_text(note), request["id"]),
            )
            audit.record(
                handle,
                actor=Actor.HUMAN,
                event_type=EventType.CHALLENGE_RESOLVED,
                outcome=str(ToolStatus.APPROVED),
                reason=note,
                metadata={
                    "request_kind": str(kind),
                    "category": request["category"],
                    "view_revision": new_revision,
                },
                occurred_at=now,
                **common,
            )
            return {"status": str(ToolStatus.APPROVED), "view_revision": new_revision}

        handle.execute(
            "UPDATE approval_requests SET status = ?, resolved_at = ?, human_note = ? WHERE id = ?",
            (str(RequestStatus.APPROVED), now, policies.sanitise_free_text(note), request["id"]),
        )
        handle.execute(
            """
            INSERT INTO reveal_grants (
                request_id, session_field_id, receipt_ref, state, field_version, issued_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request["id"],
                request["session_field_id"],
                crypto.public_ref("rcpt"),
                str(GrantState.READY),
                int(request["field_version"]),
                now,
                now + get_settings().grant_ttl_seconds,
            ),
        )
        audit.record(
            handle,
            actor=Actor.HUMAN,
            event_type=EventType.UNMASK_RESOLVED,
            outcome=str(ToolStatus.APPROVED),
            reason=note,
            metadata={
                "request_kind": str(kind),
                "category": request["category"],
                "grant_state": str(GrantState.READY),
            },
            occurred_at=now,
            **common,
        )
    return {"status": str(ToolStatus.APPROVED)}


def consume_grant(handle: sqlite3.Connection, *, session_id: int, request_ref: str) -> dict[str, Any]:
    """Exchange an approved request for its one raw response.

    The conditional UPDATE is the whole mechanism. Under concurrency exactly one
    caller matches `state = 'READY'` and gets a row back; everyone else falls
    through to the already-consumed branch. Decryption happens only after the
    transaction commits, so a crash loses the secret instead of replaying it.
    """
    now = time.time()
    session_field_id: int | None = None
    receipt_ref: str | None = None

    with immediate(handle):
        _sweep(handle, now)
        request = get_request(handle, session_id=session_id, request_ref=request_ref)
        if request is None or request["kind"] != RequestKind.UNMASK:
            return {"status": str(ToolStatus.NOT_FOUND)}

        grant = handle.execute(
            "SELECT id, state, session_field_id, receipt_ref FROM reveal_grants WHERE request_id = ?",
            (request["id"],),
        ).fetchone()
        if grant is None:
            return {"status": terminal_tool_status(request["status"])}

        claimed = handle.execute(
            """
            UPDATE reveal_grants
            SET state = ?, consumed_at = ?
            WHERE id = ?
              AND state = ?
              AND expires_at > ?
              AND field_version = (
                  SELECT version FROM session_fields WHERE id = reveal_grants.session_field_id
              )
            RETURNING session_field_id, receipt_ref
            """,
            (str(GrantState.CONSUMED), now, grant["id"], str(GrantState.READY), now),
        ).fetchone()

        if claimed is None:
            outcome = _grant_failure_status(grant["state"])
            audit.record(
                handle,
                actor=Actor.AGENT,
                event_type=EventType.REVEAL_REPLAY_REJECTED,
                outcome=str(outcome),
                session_id=session_id,
                session_document_id=int(request["session_document_id"]),
                session_field_id=int(request["session_field_id"]),
                metadata={
                    "grant_state": grant["state"],
                    "category": request["category"],
                    "receipt_id": grant["receipt_ref"],
                },
                occurred_at=now,
            )
            return {"status": str(outcome), "reveal_receipt_id": grant["receipt_ref"]}

        session_field_id = int(claimed["session_field_id"])
        receipt_ref = claimed["receipt_ref"]
        audit.record(
            handle,
            actor=Actor.AGENT,
            event_type=EventType.REVEAL_CONSUMED,
            outcome=str(ToolStatus.REVEALED),
            session_id=session_id,
            session_document_id=int(request["session_document_id"]),
            session_field_id=session_field_id,
            metadata={
                "category": request["category"],
                "grant_state": str(GrantState.CONSUMED),
                "receipt_id": receipt_ref,
            },
            occurred_at=now,
        )

    value = redaction.resolve_raw_value(handle, session_field_id=session_field_id)
    return {
        "status": str(ToolStatus.REVEALED),
        "value": value,
        "reveal_receipt_id": receipt_ref,
        "consumed": True,
    }


def _grant_failure_status(state: str) -> ToolStatus:
    if state == GrantState.CONSUMED:
        return ToolStatus.ALREADY_CONSUMED
    if state == GrantState.EXPIRED:
        return ToolStatus.TIMED_OUT
    if state == GrantState.REVOKED:
        return ToolStatus.STALE
    # READY but the conditional update still failed: the field version moved
    # between the sweep and the claim.
    return ToolStatus.STALE


def terminal_tool_status(status: str) -> str:
    mapped = REQUEST_STATUS_TO_TOOL_STATUS.get(RequestStatus(status))
    return str(mapped) if mapped else str(ToolStatus.STALE)
