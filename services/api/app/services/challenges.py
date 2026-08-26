"""Session-scoped reclassification.

An approved challenge changes what *this* session sees. It does not rewrite the
detector's output, and it does not touch any other session's view of the same
document. A false positive is a per-viewer judgement, not a correction to the
model, and treating it as the latter would let one agent widen everyone's
exposure.
"""

from __future__ import annotations

import sqlite3
import time

from ..domain import policies
from ..domain.enums import Actor, EventType, GrantState, Override, RequestStatus
from . import audit


def apply_reclassification(
    handle: sqlite3.Connection,
    *,
    session_id: int,
    session_document_id: int,
    session_field_id: int,
    field_version: int,
    category: str,
    confidence: float,
) -> int | None:
    """Flip a field to non-sensitive for one session. Caller holds the transaction.

    Returns the new view revision, or None if the field moved underneath the
    request (its version no longer matches the snapshot the human reviewed).
    """
    updated = handle.execute(
        """
        UPDATE session_fields
        SET override = ?, version = version + 1
        WHERE id = ? AND version = ?
        RETURNING version
        """,
        (str(Override.NON_SENSITIVE), session_field_id, field_version),
    ).fetchone()
    if updated is None:
        return None

    revision_row = handle.execute(
        """
        UPDATE session_documents
        SET view_revision = view_revision + 1
        WHERE id = ?
        RETURNING view_revision
        """,
        (session_document_id,),
    ).fetchone()
    new_revision = int(revision_row["view_revision"])

    # A grant issued against the old version would now hand back a value the
    # agent can simply read from the view, so it is retired rather than left live.
    handle.execute(
        "UPDATE reveal_grants SET state = ? WHERE session_field_id = ? AND state = ?",
        (str(GrantState.REVOKED), session_field_id, str(GrantState.READY)),
    )
    handle.execute(
        """
        UPDATE approval_requests
        SET status = ?, resolved_at = ?
        WHERE session_field_id = ? AND status = ?
        """,
        (str(RequestStatus.STALE), time.time(), session_field_id, str(RequestStatus.PENDING)),
    )

    audit.record(
        handle,
        actor=Actor.HUMAN,
        event_type=EventType.FIELD_RECLASSIFIED,
        outcome=str(Override.NON_SENSITIVE),
        session_id=session_id,
        session_document_id=session_document_id,
        session_field_id=session_field_id,
        metadata={
            "category": category,
            "override": str(Override.NON_SENSITIVE),
            "view_revision": new_revision,
            "field_version": int(updated["version"]),
            "confidence_band": policies.confidence_band(confidence),
        },
    )
    return new_revision


def model_assessment(*, base_entity_type: str, category: str, confidence: float) -> dict[str, object]:
    """Classifier detail, released only alongside a challenge decision.

    The masked view never carries this. A precise score on every field would let
    an agent rank which redactions are weakest and target those.
    """
    return {
        "base_entity_type": base_entity_type,
        "category": category,
        "confidence_band": policies.confidence_band(confidence),
        "confidence": round(confidence, 4),
    }
