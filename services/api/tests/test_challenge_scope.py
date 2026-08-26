"""An approved challenge changes one session's view and nothing else.

The temptation is to treat a confirmed false positive as a correction to the
detector. Doing so would let one agent's argument widen what every other viewer
of that document can see, which turns a per-viewer judgement into a global
declassification.
"""

from __future__ import annotations

import asyncio

from conftest import field_ref_for, open_document, resolve_next_request

from app.db import session as db
from app.domain.enums import GrantState, Override, RequestKind
from app.services import approvals, redaction


async def _session_id(client) -> int:
    from app.security import sessions

    row = await db.run(sessions.resolve_session, client.cookies.get("redactly_session"))
    return int(row["id"])


async def test_approved_challenge_unmasks_only_the_requesting_session(
    client, second_client, sentinel_document
):
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "MONETARY_AMOUNT")
    before = await open_document(client, slug)

    call = asyncio.create_task(
        client.post(
            f"/agent/documents/{slug}/challenge",
            json={"field_ref": field_ref, "reasoning": "a claim total is not a personal identifier"},
        )
    )
    await resolve_next_request(client, approve=True, note="agreed, false positive")
    result = (await call).json()

    assert result["status"] == "approved"
    assert result["view_revision"] == before["view_revision"] + 1
    # The classifier's own assessment is released here and only here.
    assert 0.0 <= result["model_assessment"]["confidence"] <= 1.0
    assert result["model_assessment"]["category"] == "MONETARY_AMOUNT"

    after = await open_document(client, slug)
    assert "$1,000.00" in after["masked_text"]
    assert all(field["field_ref"] != field_ref or not field["masked"] for field in after["fields"])

    other = await open_document(second_client, slug)
    assert "$1,000.00" not in other["masked_text"]
    assert any(field["category"] == "MONETARY_AMOUNT" and field["masked"] for field in other["fields"])


async def test_detector_output_is_not_rewritten(client, sentinel_document):
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "MONETARY_AMOUNT")

    call = asyncio.create_task(
        client.post(
            f"/agent/documents/{slug}/challenge",
            json={"field_ref": field_ref, "reasoning": "false positive"},
        )
    )
    await resolve_next_request(client, approve=True)
    await call

    def read(handle):
        return handle.execute(
            """
            SELECT redaction_fields.category, session_fields.override
            FROM session_fields
            JOIN redaction_fields ON redaction_fields.id = session_fields.redaction_field_id
            WHERE session_fields.public_ref = ?
            """,
            (field_ref,),
        ).fetchone()

    row = await db.run(read)
    assert row["category"] == "MONETARY_AMOUNT"
    assert row["override"] == Override.NON_SENSITIVE


async def test_rejected_challenge_leaves_the_mask_in_place(client, sentinel_document):
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "MONETARY_AMOUNT")
    before = await open_document(client, slug)

    call = asyncio.create_task(
        client.post(
            f"/agent/documents/{slug}/challenge",
            json={"field_ref": field_ref, "reasoning": "please unmask"},
        )
    )
    await resolve_next_request(client, approve=False, note="settlement figures stay masked")
    result = (await call).json()

    assert result["status"] == "rejected"
    after = await open_document(client, slug)
    assert after["view_revision"] == before["view_revision"]
    assert "$1,000.00" not in after["masked_text"]


async def test_reclassification_revokes_a_live_reveal_grant(client, sentinel_document):
    """A grant issued before the field moved must not survive the move.

    Otherwise an agent could hold an approved grant, get the field reclassified,
    and still spend the grant on a value it can now simply read — two paths to
    the same value from one human decision.
    """
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "MONETARY_AMOUNT")
    session_id = await _session_id(client)

    def create(handle):
        field = redaction.get_session_field(handle, session_id=session_id, field_ref=field_ref)
        return approvals.create_request(
            handle,
            session_id=session_id,
            field=field,
            kind=RequestKind.UNMASK,
            reason="hold a grant open",
        )

    created = await db.run(create)
    await resolve_next_request(client, approve=True)

    challenge = asyncio.create_task(
        client.post(
            f"/agent/documents/{slug}/challenge",
            json={"field_ref": field_ref, "reasoning": "false positive"},
        )
    )
    await resolve_next_request(client, approve=True)
    assert (await challenge).json()["status"] == "approved"

    consumed = await db.run(
        approvals.consume_grant, session_id=session_id, request_ref=created["request_ref"]
    )
    assert consumed["status"] == "stale"
    assert "value" not in consumed

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

    assert await db.run(grant_state) == GrantState.REVOKED


async def test_cursor_from_a_previous_revision_is_stale(client, sentinel_document, settings_override):
    settings_override(view_page_characters=120)
    slug = sentinel_document["slug"]

    first_page = await open_document(client, slug)
    assert first_page["has_more"] is True
    stale_cursor = first_page["next_cursor"]

    field_ref = next(
        field["field_ref"]
        for field in first_page["fields"]
        if field["masked"] and field["category"] == "EMAIL"
    )
    call = asyncio.create_task(
        client.post(
            f"/agent/documents/{slug}/challenge",
            json={"field_ref": field_ref, "reasoning": "test-only reclassification"},
        )
    )
    await resolve_next_request(client, approve=True)
    await call

    response = await client.post(
        f"/agent/documents/{slug}/view", json={"purpose": "resume with old cursor", "cursor": stale_cursor}
    )
    body = response.json()
    assert body["status"] == "stale"
    assert body["reason"] == "view_revision_changed"
