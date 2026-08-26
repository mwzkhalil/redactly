"""`verify_without_reveal` must not become a brute-force oracle.

An equality check against a hidden value is only safe while the number of
questions is bounded. These tests pin the bound, confirm the comparison is
format-tolerant enough to be useful, and confirm a rejected attempt still costs
the caller a slot — otherwise the limit could be evaded with wrong guesses.
"""

from __future__ import annotations

from conftest import field_ref_for


async def test_correct_guess_matches_without_returning_the_value(client, sentinel_document):
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "EMAIL")

    response = await client.post(
        f"/agent/documents/{slug}/verify",
        json={
            "field_ref": field_ref,
            "comparison_value": sentinel_document["sentinel"].upper(),
            "purpose": "case-insensitive email check",
        },
    )
    body = response.json()
    assert body["status"] == "checked"
    assert body["matches"] is True
    assert "value" not in body
    assert sentinel_document["sentinel"] not in response.text


async def test_phone_canonicalisation_tolerates_formatting(client, sentinel_document):
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "PHONE")

    for candidate in ("+1 202-555-0199", "(202) 555 0199", "2025550199"):
        response = await client.post(
            f"/agent/documents/{slug}/verify",
            json={"field_ref": field_ref, "comparison_value": candidate, "purpose": "format tolerance"},
        )
        assert response.json()["matches"] is True, candidate


async def test_wrong_guess_reports_no_match(client, sentinel_document):
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "PHONE")
    response = await client.post(
        f"/agent/documents/{slug}/verify",
        json={"field_ref": field_ref, "comparison_value": "(202) 555-0100", "purpose": "negative"},
    )
    assert response.json()["matches"] is False


async def test_rate_limit_stops_enumeration(client, sentinel_document, settings_override):
    settings_override(verify_max_per_field=3, verify_window_seconds=60)
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "PHONE")

    statuses = []
    for index in range(6):
        response = await client.post(
            f"/agent/documents/{slug}/verify",
            json={
                "field_ref": field_ref,
                "comparison_value": f"(202) 555-01{index:02d}",
                "purpose": "enumeration attempt",
            },
        )
        statuses.append(response.json()["status"])

    assert statuses[:3] == ["checked", "checked", "checked"]
    assert set(statuses[3:]) == {"rate_limited"}


async def test_rate_limited_response_carries_a_retry_hint(client, sentinel_document, settings_override):
    settings_override(verify_max_per_field=1, verify_window_seconds=45)
    slug = sentinel_document["slug"]
    field_ref = await field_ref_for(client, slug, "PHONE")

    await client.post(
        f"/agent/documents/{slug}/verify",
        json={"field_ref": field_ref, "comparison_value": "x", "purpose": "first"},
    )
    second = await client.post(
        f"/agent/documents/{slug}/verify",
        json={"field_ref": field_ref, "comparison_value": "y", "purpose": "second"},
    )
    body = second.json()
    assert body["status"] == "rate_limited"
    assert body["retry_after_seconds"] == 45
    assert body["scope"] in {"field", "session"}


async def test_session_wide_limit_applies_across_fields(client, sentinel_document, settings_override):
    settings_override(verify_max_per_field=50, verify_max_per_session=2, verify_window_seconds=60)
    slug = sentinel_document["slug"]
    email_ref = await field_ref_for(client, slug, "EMAIL")
    phone_ref = await field_ref_for(client, slug, "PHONE")

    statuses = []
    for field_ref in (email_ref, phone_ref, email_ref, phone_ref):
        response = await client.post(
            f"/agent/documents/{slug}/verify",
            json={"field_ref": field_ref, "comparison_value": "probe", "purpose": "spread the load"},
        )
        statuses.append(response.json()["status"])

    assert statuses[:2] == ["checked", "checked"]
    assert set(statuses[2:]) == {"rate_limited"}


async def test_digests_are_not_comparable_across_documents(sentinel_document):
    """Two fields holding the same value must not share a digest.

    Equal digests would make the column a correlation oracle: an operator (or
    anything with read access) could tell that two documents name the same person
    without ever decrypting either.
    """
    from app.db import session as db
    from app.services import redaction

    shared = "shared.person@example.invalid"
    body = f"Sentinel Fixture\n\nEmail: {shared}\n"

    def ingest(handle):
        redaction.ingest_document(handle, slug="digest-probe-a", title="A", text=body)
        redaction.ingest_document(handle, slug="digest-probe-b", title="B", text=body)
        return handle.execute(
            """
            SELECT redaction_fields.canonical_hmac
            FROM redaction_fields
            JOIN documents ON documents.id = redaction_fields.document_id
            WHERE documents.slug IN ('digest-probe-a', 'digest-probe-b')
              AND redaction_fields.category = 'EMAIL'
            """
        ).fetchall()

    rows = await db.run(ingest)
    assert len(rows) == 2
    assert bytes(rows[0]["canonical_hmac"]) != bytes(rows[1]["canonical_hmac"])
