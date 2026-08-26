"""Test harness.

Environment is configured before any application module is imported, because
`get_settings` is cached for the process lifetime. The deterministic detector is
selected so that a failing security assertion always means a security regression
rather than a model revision changing a span boundary.
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import tempfile
import uuid
from pathlib import Path

_TEMP_ROOT = Path(tempfile.mkdtemp(prefix="redactly-tests-"))

os.environ["REDACTLY_MASTER_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
os.environ["REDACTLY_DATABASE_PATH"] = str(_TEMP_ROOT / "redactly.sqlite3")
os.environ["REDACTLY_DETECTOR"] = "deterministic"
os.environ["REDACTLY_APPROVAL_TIMEOUT_SECONDS"] = "20"
os.environ["REDACTLY_GRANT_TTL_SECONDS"] = "20"
os.environ["REDACTLY_COOKIE_SECURE"] = "0"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import session as db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import redaction  # noqa: E402

SENTINEL_DOMAIN = "example.invalid"


@pytest.fixture(scope="session", autouse=True)
def prepared_database() -> None:
    db.initialise()
    with db.connection() as handle:
        redaction.seed_from_fixtures(handle, get_settings().fixtures_dir)


@pytest.fixture
def settings_override():
    """Temporarily change a setting that is normally read from the environment."""
    original = dict(os.environ)

    def apply(**overrides: object) -> None:
        for key, value in overrides.items():
            os.environ[f"REDACTLY_{key.upper()}"] = str(value)
        get_settings.cache_clear()

    yield apply

    os.environ.clear()
    os.environ.update(original)
    get_settings.cache_clear()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as instance:
        response = await instance.post("/session")
        assert response.status_code == 200
        yield instance


@pytest.fixture
async def second_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as instance:
        await instance.post("/session")
        yield instance


@pytest.fixture
def sentinel_document():
    """Ingest a document whose email field holds a value unique to this test run.

    A unique sentinel is what makes the no-leak sweep meaningful: any occurrence
    anywhere other than the single reveal response is unambiguously a leak.
    """
    token = uuid.uuid4().hex
    sentinel = f"s3ntinel-{token}@{SENTINEL_DOMAIN}"
    slug = f"sentinel-{token[:16]}"
    text = (
        "Sentinel Fixture\n\n"
        "SYNTHETIC TEST DOCUMENT\n\n"
        f"Email: {sentinel}\n"
        "Phone: (202) 555-0199\n"
        "Policyholder: Testcase Subject\n"
        "Claimed amount: $1,000.00\n\n"
        "Body text that is not sensitive and should survive masking intact.\n"
    )
    with db.connection() as handle:
        redaction.ingest_document(handle, slug=slug, title="Sentinel Fixture", text=text)
    return {"slug": slug, "sentinel": sentinel, "text": text}


async def open_document(client: AsyncClient, slug: str, purpose: str = "test") -> dict:
    response = await client.post(f"/agent/documents/{slug}/view", json={"purpose": purpose})
    assert response.status_code == 200, response.text
    return response.json()


async def resolve_next_request(
    client: AsyncClient,
    *,
    approve: bool = True,
    note: str | None = None,
    attempts: int = 400,
) -> dict:
    """Act as the human owner: wait for a queued request and decide it."""
    for _ in range(attempts):
        pending = (await client.get("/owner/pending")).json()["requests"]
        if pending:
            request_ref = pending[0]["request_ref"]
            response = await client.post(
                f"/owner/requests/{request_ref}/decision",
                json={"approve": approve, "note": note},
            )
            assert response.status_code == 200, response.text
            return response.json()
        await asyncio.sleep(0.02)
    raise AssertionError("no pending approval request appeared")


async def field_ref_for(client: AsyncClient, slug: str, category: str) -> str:
    page = await open_document(client, slug)
    for field in page["fields"]:
        if field["category"] == category and field["masked"]:
            return field["field_ref"]
    raise AssertionError(f"no masked {category} field on page 0 of {slug}: {page['fields']}")
