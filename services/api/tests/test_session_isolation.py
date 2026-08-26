"""One browser's field references are useless in another browser.

Placeholder ids are minted per session, and every lookup joins through the
session derived from the cookie. Two consequences are tested here: references
cannot be replayed across sessions, and an unknown reference is indistinguishable
from someone else's reference, so the endpoint cannot be used to confirm that a
given id exists.
"""

from __future__ import annotations

from conftest import field_ref_for, open_document


async def test_field_references_differ_between_sessions(client, second_client, sentinel_document):
    slug = sentinel_document["slug"]
    first = await field_ref_for(client, slug, "EMAIL")
    second = await field_ref_for(second_client, slug, "EMAIL")
    assert first != second


async def test_foreign_reference_is_rejected_by_every_tool(client, second_client, sentinel_document):
    slug = sentinel_document["slug"]
    foreign = await field_ref_for(client, slug, "EMAIL")

    verify = await second_client.post(
        f"/agent/documents/{slug}/verify",
        json={
            "field_ref": foreign,
            "comparison_value": sentinel_document["sentinel"],
            "purpose": "cross-session probe",
        },
    )
    assert verify.status_code == 404

    unmask = await second_client.post(
        f"/agent/documents/{slug}/unmask",
        json={"field_ref": foreign, "reason": "cross-session probe"},
    )
    assert unmask.status_code == 404

    challenge = await second_client.post(
        f"/agent/documents/{slug}/challenge",
        json={"field_ref": foreign, "reasoning": "cross-session probe"},
    )
    assert challenge.status_code == 404


async def test_unknown_and_foreign_references_are_indistinguishable(client, second_client, sentinel_document):
    slug = sentinel_document["slug"]
    foreign = await field_ref_for(client, slug, "EMAIL")
    invented = "fld_totallyMadeUpReference"

    foreign_response = await second_client.post(
        f"/agent/documents/{slug}/verify",
        json={"field_ref": foreign, "comparison_value": "x", "purpose": "probe"},
    )
    invented_response = await second_client.post(
        f"/agent/documents/{slug}/verify",
        json={"field_ref": invented, "comparison_value": "x", "purpose": "probe"},
    )
    assert foreign_response.status_code == invented_response.status_code == 404
    assert foreign_response.json() == invented_response.json()


async def test_audit_log_is_scoped_to_the_calling_session(client, second_client, sentinel_document):
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "EMAIL")
    await client.post(
        f"/agent/documents/{slug}/verify",
        json={"field_ref": field_ref, "comparison_value": "guess", "purpose": "leaves a trace"},
    )

    await open_document(second_client, slug)
    other_log = await second_client.post(
        f"/agent/documents/{slug}/audit", json={"purpose": "read neighbour log", "limit": 50}
    )
    events = other_log.json()["events"]
    assert all(event["event_type"] != "FIELD_VERIFIED" for event in events)
    assert all(event["field_ref"] != field_ref for event in events)
