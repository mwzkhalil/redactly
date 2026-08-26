"""A raw field value appears in exactly one place: the single reveal response.

The method is deliberately blunt. Exercise every tool, capture everything the
system emits — responses, audit rows, every column of every table, and the log
stream — then search all of it for a value that exists nowhere else in the
universe. A subtle leak is still a substring.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

from conftest import field_ref_for, resolve_next_request

from app.db import session as db
from app.security.log_filters import JsonFormatter, SensitiveFieldFilter


def _dump_every_row(handle: sqlite3.Connection) -> str:
    """Serialise the entire database, including blobs, as one searchable string."""
    tables = [
        row["name"]
        for row in handle.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    chunks: list[str] = []
    for table in tables:
        for row in handle.execute(f"SELECT * FROM {table}").fetchall():  # noqa: S608 - table names from sqlite_master
            for value in tuple(row):
                if isinstance(value, bytes):
                    chunks.append(value.decode("utf-8", errors="replace"))
                else:
                    chunks.append(str(value))
    return "\n".join(chunks)


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.setFormatter(JsonFormatter())
        self.addFilter(SensitiveFieldFilter())

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


async def test_sentinel_never_escapes_outside_the_single_reveal(client, sentinel_document):
    slug = sentinel_document["slug"]
    sentinel = sentinel_document["sentinel"]
    observed: list[str] = []

    capture = _CapturingHandler()
    root = logging.getLogger()
    root.addHandler(capture)

    try:
        view = await client.post(f"/agent/documents/{slug}/view", json={"purpose": "leak sweep"})
        observed.append(view.text)
        field_ref = await field_ref_for(client, slug, "EMAIL")

        # A correct guess: the endpoint confirms the match and must still not
        # echo the value it just confirmed.
        verified = await client.post(
            f"/agent/documents/{slug}/verify",
            json={"field_ref": field_ref, "comparison_value": sentinel, "purpose": "cross-check"},
        )
        observed.append(verified.text)
        assert verified.json()["matches"] is True

        wrong = await client.post(
            f"/agent/documents/{slug}/verify",
            json={
                "field_ref": field_ref,
                "comparison_value": "someone.else@example.invalid",
                "purpose": "negative control",
            },
        )
        observed.append(wrong.text)
        assert wrong.json()["matches"] is False

        denied = asyncio.create_task(
            client.post(
                f"/agent/documents/{slug}/unmask",
                json={"field_ref": field_ref, "reason": "first attempt, expect denial"},
            )
        )
        await resolve_next_request(client, approve=False, note="not justified")
        observed.append((await denied).text)

        approved = asyncio.create_task(
            client.post(
                f"/agent/documents/{slug}/unmask",
                json={"field_ref": field_ref, "reason": "second attempt, expect approval"},
            )
        )
        await resolve_next_request(client, approve=True, note="release once")
        reveal = await approved

        # The one permitted occurrence.
        assert reveal.json()["value"] == sentinel

        replay = await client.post(
            f"/agent/documents/{slug}/unmask/{reveal.json()['request_ref']}/retrieve"
        )
        observed.append(replay.text)

        audit = await client.post(
            f"/agent/documents/{slug}/audit", json={"purpose": "leak sweep", "limit": 50}
        )
        observed.append(audit.text)

        masked = await client.get(f"/documents/{slug}/masked")
        observed.append(masked.text)

        pending = await client.get("/owner/pending")
        observed.append(pending.text)

        database_dump = await db.run(_dump_every_row)
    finally:
        root.removeHandler(capture)

    for payload in observed:
        assert sentinel not in payload, f"sentinel leaked into a non-reveal response: {payload[:400]}"

    assert sentinel not in database_dump, "sentinel is recoverable from the database in plaintext"
    assert sentinel not in "\n".join(capture.lines), "sentinel reached the log stream"


async def test_audit_metadata_holds_no_free_form_values(client, sentinel_document):
    slug = sentinel_document["slug"]
    sentinel = sentinel_document["sentinel"]
    field_ref = await field_ref_for(client, slug, "EMAIL")

    await client.post(
        f"/agent/documents/{slug}/verify",
        json={"field_ref": field_ref, "comparison_value": sentinel, "purpose": "audit shape check"},
    )
    response = await client.post(
        f"/agent/documents/{slug}/audit", json={"purpose": "audit shape check", "limit": 50}
    )
    events = response.json()["events"]
    assert events

    from app.domain.policies import ALLOWED_METADATA_KEYS

    for event in events:
        assert set(event["metadata"]).issubset(ALLOWED_METADATA_KEYS), event["metadata"]
        # Metadata must stay scalar. Nested structures are how a raw value ends
        # up somewhere nobody thought to check.
        assert all(not isinstance(value, (dict, list)) for value in event["metadata"].values())


async def test_verification_guess_is_never_persisted(client, sentinel_document):
    slug = sentinel_document["slug"]
    guess = "unique-guess-not-in-any-document@example.invalid"
    field_ref = await field_ref_for(client, slug, "EMAIL")

    await client.post(
        f"/agent/documents/{slug}/verify",
        json={"field_ref": field_ref, "comparison_value": guess, "purpose": "guess retention check"},
    )
    dump = await db.run(_dump_every_row)
    assert guess not in dump


async def test_validation_errors_do_not_echo_the_argument(client, sentinel_document):
    slug = sentinel_document["slug"]
    oversized = "x" * 500
    response = await client.post(
        f"/agent/documents/{slug}/verify",
        json={"field_ref": "fld_not_a_real_reference", "comparison_value": oversized, "purpose": "p"},
    )
    assert response.status_code == 422
    body = json.loads(response.text)
    assert body["status"] == "invalid_arguments"
    assert oversized not in response.text
