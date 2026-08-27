"""A service restart must not unmask a document for a session that outlives it.

This is a regression suite for a live leak. Seeding ran on every boot and
re-ingested each fixture unconditionally; re-ingesting deletes and recreates
`redaction_fields`, and `session_fields` cascades from those rows. A browser
holding a session cookie from before the restart therefore kept its
`session_documents` row while losing every field reference under it — and
because masking is derived only from those references, the viewer rendered the
document with nothing redacted at all.

The value of these tests is the pairing: one asserts the destructive re-ingest no
longer happens, the others assert that the render paths refuse rather than leak
even if it somehow does.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.db import session as db
from app.services import redaction, view

from conftest import open_document


async def test_reseeding_fixtures_keeps_placeholder_ids_stable(client):
    """Seeding unchanged fixtures must be a no-op, ids included.

    Stable references are the observable consequence of not re-ingesting. If the
    ids move, the rows were recreated, which means some other session's mapping
    was just cascaded away.
    """
    slug = "claim-northstar-4471"
    before = await open_document(client, slug)
    references = {field["field_ref"] for field in before["fields"]}
    assert references, "fixture produced no fields, so this test proves nothing"

    with db.connection() as handle:
        redaction.seed_from_fixtures(handle, get_settings().fixtures_dir)

    after = await open_document(client, slug)
    assert after["status"] == "ok"
    assert {field["field_ref"] for field in after["fields"]} == references


async def test_agent_view_survives_a_destructive_reingest(client, sentinel_document):
    """Force the cascade, then confirm the agent view still masks.

    Calling `ingest_document` directly is the restart, reduced to the one step
    that caused the damage.
    """
    slug = sentinel_document["slug"]
    sentinel = sentinel_document["sentinel"]

    first = await open_document(client, slug)
    assert sentinel not in first["masked_text"]

    with db.connection() as handle:
        redaction.ingest_document(
            handle, slug=slug, title="Sentinel Fixture", text=sentinel_document["text"]
        )

    second = await open_document(client, slug)
    assert second["status"] == "ok"
    assert sentinel not in second["masked_text"], "re-ingest unmasked a live session"
    assert any(field["masked"] for field in second["fields"])


async def test_human_view_survives_a_destructive_reingest(client, sentinel_document):
    """The owner viewer is the path that actually leaked, so it is asserted too."""
    slug = sentinel_document["slug"]
    sentinel = sentinel_document["sentinel"]

    assert (await client.get(f"/documents/{slug}/masked")).status_code == 200

    with db.connection() as handle:
        redaction.ingest_document(
            handle, slug=slug, title="Sentinel Fixture", text=sentinel_document["text"]
        )

    response = await client.get(f"/documents/{slug}/masked")
    assert response.status_code == 200, response.text
    assert sentinel not in response.text
    assert any(field["masked"] for field in response.json()["fields"])


async def test_build_view_refuses_an_incomplete_mapping(client, sentinel_document):
    """The safety net on its own, with the repair bypassed.

    `build_view` is called directly because going through the API would repair
    the mapping first. Both behaviours are wanted: repair when reachable, refuse
    when not. Emitting the raw span is the one outcome that must be impossible.
    """
    slug = sentinel_document["slug"]
    await open_document(client, slug)

    with db.connection() as handle:
        session_document = handle.execute(
            """
            SELECT session_documents.*, documents.slug, documents.title
            FROM session_documents
            JOIN documents ON documents.id = session_documents.document_id
            WHERE documents.slug = ?
            """,
            (slug,),
        ).fetchone()
        handle.execute(
            "DELETE FROM session_fields WHERE session_document_id = ?",
            (int(session_document["id"]),),
        )
        handle.commit()

        result = view.build_view(handle, session_document=session_document, cursor=None)

    assert result["status"] == "unavailable"
    assert result["reason"] == "view_not_materialised"
    assert "masked_text" not in result


async def test_api_repairs_rather_than_refuses(client, sentinel_document):
    """Through the API the same damage is repaired, so a visitor is not stranded.

    Failing closed protects the data; repairing keeps the app usable. A guard
    that only ever refused would turn one bad restart into a permanently broken
    session.
    """
    slug = sentinel_document["slug"]
    await open_document(client, slug)

    with db.connection() as handle:
        handle.execute(
            """
            DELETE FROM session_fields WHERE session_document_id IN (
                SELECT session_documents.id
                FROM session_documents
                JOIN documents ON documents.id = session_documents.document_id
                WHERE documents.slug = ?
            )
            """,
            (slug,),
        )
        handle.commit()

    page = await open_document(client, slug)
    assert page["status"] == "ok"
    assert sentinel_document["sentinel"] not in page["masked_text"]
    assert any(field["masked"] for field in page["fields"])


@pytest.mark.parametrize("missing", [1, 3])
async def test_unmapped_field_count_reports_the_shortfall(client, sentinel_document, missing):
    """The guard's input, verified directly.

    A count that silently returned zero would disable the fail-closed branch
    everywhere without any test failing.
    """
    slug = sentinel_document["slug"]
    await open_document(client, slug)

    with db.connection() as handle:
        row = handle.execute(
            """
            SELECT session_documents.id
            FROM session_documents
            JOIN documents ON documents.id = session_documents.document_id
            WHERE documents.slug = ?
            """,
            (slug,),
        ).fetchone()
        session_document_id = int(row["id"])
        assert redaction.unmapped_field_count(
            handle, session_document_id=session_document_id
        ) == 0

        doomed = handle.execute(
            "SELECT id FROM session_fields WHERE session_document_id = ? LIMIT ?",
            (session_document_id, missing),
        ).fetchall()
        if len(doomed) < missing:
            pytest.skip("sentinel fixture has too few fields for this shortfall")
        handle.executemany(
            "DELETE FROM session_fields WHERE id = ?",
            [(int(item["id"]),) for item in doomed],
        )
        handle.commit()

        assert redaction.unmapped_field_count(
            handle, session_document_id=session_document_id
        ) == missing
