"""Deadlines fail closed.

Every waiting path has to end in a refusal rather than a reveal. A request nobody
answers, a grant nobody spends, and a caller that disappears all resolve to a
terminal non-revealing state.
"""

from __future__ import annotations

import asyncio

from conftest import field_ref_for, resolve_next_request

from app.db import session as db
from app.domain.enums import GrantState, RequestKind, RequestStatus
from app.services import approvals, redaction


async def _session_id(client) -> int:
    from app.security import sessions

    row = await db.run(sessions.resolve_session, client.cookies.get("redactly_session"))
    return int(row["id"])


async def test_unanswered_unmask_times_out_without_revealing(client, sentinel_document, settings_override):
    settings_override(approval_timeout_seconds=1)
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "EMAIL")

    response = await client.post(
        f"/agent/documents/{slug}/unmask",
        json={"field_ref": field_ref, "reason": "nobody is going to answer this"},
    )
    body = response.json()
    assert body["status"] in {"timed_out", "cancelled"}
    assert "value" not in body
    assert sentinel_document["sentinel"] not in response.text


async def test_expired_grant_cannot_be_consumed(client, sentinel_document, settings_override):
    settings_override(grant_ttl_seconds=1)
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "EMAIL")
    session_id = await _session_id(client)

    def create(handle):
        field = redaction.get_session_field(handle, session_id=session_id, field_ref=field_ref)
        return approvals.create_request(
            handle,
            session_id=session_id,
            field=field,
            kind=RequestKind.UNMASK,
            reason="approve then let it expire",
        )

    created = await db.run(create)
    await resolve_next_request(client, approve=True)
    await asyncio.sleep(1.3)

    result = await db.run(
        approvals.consume_grant, session_id=session_id, request_ref=created["request_ref"]
    )
    assert result["status"] == "timed_out"
    assert "value" not in result

    def grant_state(handle):
        return handle.execute(
            """
            SELECT reveal_grants.state
            FROM reveal_grants
            JOIN approval_requests ON approval_requests.id = reveal_grants.request_id
            WHERE approval_requests.public_ref = ?
            """,
            (created["request_ref"],),
        ).fetchone()["state"]

    assert await db.run(grant_state) == GrantState.EXPIRED


async def test_timeout_is_recorded_as_a_system_decision(client, sentinel_document, settings_override):
    settings_override(approval_timeout_seconds=1)
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "EMAIL")

    await client.post(
        f"/agent/documents/{slug}/unmask",
        json={"field_ref": field_ref, "reason": "expect a system timeout in the log"},
    )
    log = await client.post(f"/agent/documents/{slug}/audit", json={"purpose": "check", "limit": 50})
    events = log.json()["events"]
    resolutions = [event for event in events if event["event_type"] == "UNMASK_RESOLVED"]
    assert resolutions
    assert all(event["actor"] in {"SYSTEM", "HUMAN"} for event in resolutions)
    # An agent must never be recorded as the actor that resolved its own request.
    assert all(event["actor"] != "AGENT" for event in resolutions)


async def test_superseded_request_is_cancelled(client, sentinel_document, settings_override):
    settings_override(approval_timeout_seconds=20)
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "EMAIL")
    session_id = await _session_id(client)

    def create(handle):
        field = redaction.get_session_field(handle, session_id=session_id, field_ref=field_ref)
        return approvals.create_request(
            handle,
            session_id=session_id,
            field=field,
            kind=RequestKind.UNMASK,
            reason="first request",
        )

    first = await db.run(create)
    second = await db.run(create)
    assert first["request_ref"] != second["request_ref"]

    def status_of(handle, request_ref: str) -> str:
        return handle.execute(
            "SELECT status FROM approval_requests WHERE public_ref = ?", (request_ref,)
        ).fetchone()["status"]

    assert await db.run(status_of, first["request_ref"]) == RequestStatus.CANCELLED
    assert await db.run(status_of, second["request_ref"]) == RequestStatus.PENDING

    pending = (await client.get("/owner/pending")).json()["requests"]
    assert len(pending) == 1
    assert pending[0]["request_ref"] == second["request_ref"]
