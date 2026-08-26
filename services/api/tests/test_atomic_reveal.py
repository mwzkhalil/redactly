"""A reveal grant is consumed exactly once, including under concurrency.

This is the load-bearing test for the whole design. If two concurrent callers can
both consume one approval, then "at most once" is a comment rather than a
property, and every other guarantee downstream of it is decorative.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import field_ref_for, resolve_next_request

from app.db import session as db
from app.domain.enums import GrantState, RequestKind
from app.services import approvals, redaction

CONCURRENT_CONSUMERS = 50


async def _approved_request(client, slug: str) -> tuple[str, int]:
    """Drive an unmask to APPROVED without consuming the grant.

    The tool route consumes immediately on approval, so the request is created
    through the service layer to leave a READY grant sitting there.
    """
    field_ref = await field_ref_for(client, slug, "EMAIL")
    session_id = await _session_id(client)

    def create(handle):
        field = redaction.get_session_field(handle, session_id=session_id, field_ref=field_ref)
        return approvals.create_request(
            handle,
            session_id=session_id,
            field=field,
            kind=RequestKind.UNMASK,
            reason="concurrency probe",
        )

    created = await db.run(create)
    assert created["status"] == "pending"

    decision = await resolve_next_request(client, approve=True, note="approved for test")
    assert decision["status"] == "approved"
    return created["request_ref"], session_id


async def _session_id(client) -> int:
    from app.security import sessions

    token = client.cookies.get("redactly_session")
    row = await db.run(sessions.resolve_session, token)
    assert row is not None
    return int(row["id"])


async def test_single_consume_returns_the_value(client, sentinel_document):
    slug = sentinel_document["slug"]
    request_ref, session_id = await _approved_request(client, slug)

    first = await db.run(approvals.consume_grant, session_id=session_id, request_ref=request_ref)
    assert first["status"] == "revealed"
    assert first["value"] == sentinel_document["sentinel"]
    assert first["consumed"] is True
    assert first["reveal_receipt_id"].startswith("rcpt_")

    replay = await db.run(approvals.consume_grant, session_id=session_id, request_ref=request_ref)
    assert replay["status"] == "already_consumed"
    assert "value" not in replay
    # The receipt comes back so an auditor can correlate the two attempts, and it
    # is demonstrably not a bearer token for the value.
    assert replay["reveal_receipt_id"] == first["reveal_receipt_id"]


@pytest.mark.parametrize("consumers", [CONCURRENT_CONSUMERS])
async def test_concurrent_consumers_get_exactly_one_value(client, sentinel_document, consumers):
    slug = sentinel_document["slug"]
    sentinel = sentinel_document["sentinel"]
    request_ref, session_id = await _approved_request(client, slug)

    results = await asyncio.gather(
        *(
            db.run(approvals.consume_grant, session_id=session_id, request_ref=request_ref)
            for _ in range(consumers)
        )
    )

    revealed = [item for item in results if item["status"] == "revealed"]
    rejected = [item for item in results if item["status"] == "already_consumed"]

    assert len(revealed) == 1, [item["status"] for item in results]
    assert len(rejected) == consumers - 1
    assert revealed[0]["value"] == sentinel
    assert all(sentinel not in repr(item) for item in rejected)


async def test_consumed_grant_is_terminal_in_the_database(client, sentinel_document):
    request_ref, session_id = await _approved_request(client, sentinel_document["slug"])
    await db.run(approvals.consume_grant, session_id=session_id, request_ref=request_ref)

    def read_state(handle):
        return handle.execute(
            """
            SELECT reveal_grants.state, reveal_grants.consumed_at
            FROM reveal_grants
            JOIN approval_requests ON approval_requests.id = reveal_grants.request_id
            WHERE approval_requests.public_ref = ?
            """,
            (request_ref,),
        ).fetchone()

    row = await db.run(read_state)
    assert row["state"] == GrantState.CONSUMED
    assert row["consumed_at"] is not None


async def test_end_to_end_tool_call_reveals_once(client, sentinel_document):
    """The same guarantee through the HTTP tool surface an agent actually calls."""
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "EMAIL")

    call = asyncio.create_task(
        client.post(
            f"/agent/documents/{slug}/unmask",
            json={"field_ref": field_ref, "reason": "verifying claimant contact details"},
        )
    )
    await resolve_next_request(client, approve=True)
    payload = (await call).json()

    assert payload["status"] == "revealed"
    assert payload["value"] == sentinel_document["sentinel"]

    replay = await client.post(f"/agent/documents/{slug}/unmask/{payload['request_ref']}/retrieve")
    assert replay.json()["status"] == "already_consumed"
    assert "value" not in replay.json()
