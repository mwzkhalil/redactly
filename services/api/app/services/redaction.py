"""Document ingestion, per-session materialisation, and raw value resolution.

`resolve_raw_value` is the only function in the codebase that turns a field
reference back into text. Keeping it in one place is what makes the no-leak test
tractable: every caller of it is auditable by grep.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from ..db.session import immediate
from ..domain import policies
from ..domain.enums import Actor, EventType, Override, ProcessingStatus
from ..inference import DetectorUnavailable, get_detector
from ..security import crypto
from ..security.canonical import CANONICALIZATION_VERSION, canonicalise
from . import audit


@dataclass(frozen=True, slots=True)
class FieldView:
    """A field as seen through one session."""

    session_field_id: int
    field_ref: str
    start_offset: int
    end_offset: int
    category: str
    base_entity_type: str
    confidence: float
    safe_context_hint: str
    override: str
    version: int

    @property
    def masked(self) -> bool:
        return self.override != Override.NON_SENSITIVE and policies.is_sensitive(self.category)


def ingest_document(handle: sqlite3.Connection, *, slug: str, title: str, text: str) -> int:
    """Detect, encrypt, and store a document. Detection runs before any write.

    If the detector is unavailable the document is stored as FAILED with no
    fields, and `open_document` refuses to build a view from it. A document that
    was never scanned must never render as though it had been.
    """
    now = time.time()
    digest = crypto.content_digest(text)
    ciphertext, nonce = crypto.encrypt_document(slug, text)

    spans: list = []
    status = ProcessingStatus.FAILED
    model_name, model_revision, onnx_sha256 = "unavailable", None, None
    try:
        detector = get_detector()
        model_name, model_revision, onnx_sha256 = detector.name, detector.revision, detector.onnx_sha256
        spans = detector.detect(text)
        status = ProcessingStatus.READY
    except DetectorUnavailable:
        # Stored as FAILED with no fields. `open_document` refuses to build a view
        # from it, so an unscanned document can never render as though it had been
        # scanned and found clean.
        pass

    with immediate(handle):
        cursor = handle.execute(
            """
            INSERT INTO documents (
                slug, title, encrypted_text, nonce, content_sha256,
                processing_status, model_name, model_revision, onnx_sha256,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (slug) DO UPDATE SET
                title = excluded.title,
                encrypted_text = excluded.encrypted_text,
                nonce = excluded.nonce,
                content_sha256 = excluded.content_sha256,
                processing_status = excluded.processing_status,
                model_name = excluded.model_name,
                model_revision = excluded.model_revision,
                onnx_sha256 = excluded.onnx_sha256,
                updated_at = excluded.updated_at
            RETURNING id
            """,
            (
                slug,
                title,
                ciphertext,
                nonce,
                digest,
                str(status),
                model_name,
                model_revision,
                onnx_sha256,
                now,
                now,
            ),
        )
        document_id = int(cursor.fetchone()["id"])
        handle.execute("DELETE FROM redaction_fields WHERE document_id = ?", (document_id,))

        for span in spans:
            raw_value = text[span.start : span.end]
            separator = crypto.field_domain_separator(slug, digest, span.start, span.end)
            handle.execute(
                """
                INSERT INTO redaction_fields (
                    document_id, start_offset, end_offset, base_entity_type, category,
                    confidence, canonical_hmac, canonicalization_version, safe_context_hint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    span.start,
                    span.end,
                    span.base_entity_type,
                    span.category,
                    span.confidence,
                    crypto.canonical_digest(canonicalise(raw_value, span.category), separator),
                    CANONICALIZATION_VERSION,
                    policies.safe_context_hint(raw_value, span.category),
                ),
            )
    return document_id


def list_documents(handle: sqlite3.Connection) -> list[dict[str, object]]:
    rows = handle.execute(
        """
        SELECT documents.slug,
               documents.title,
               documents.processing_status,
               COUNT(redaction_fields.id) AS field_count
        FROM documents
        LEFT JOIN redaction_fields ON redaction_fields.document_id = documents.id
        GROUP BY documents.id
        ORDER BY documents.id
        """
    ).fetchall()
    return [
        {
            "slug": row["slug"],
            "title": row["title"],
            "processing_status": row["processing_status"],
            "field_count": int(row["field_count"]),
        }
        for row in rows
    ]


def open_document(handle: sqlite3.Connection, *, session_id: int, slug: str) -> sqlite3.Row | None:
    """Materialise per-session refs for a document, idempotently.

    Field references are minted per session, so two judges looking at the same
    document never share a placeholder id and cannot correlate their views.
    """
    document = handle.execute(
        "SELECT id, processing_status FROM documents WHERE slug = ?",
        (slug,),
    ).fetchone()
    if document is None or document["processing_status"] != ProcessingStatus.READY:
        return None

    with immediate(handle):
        handle.execute(
            """
            INSERT INTO session_documents (session_id, document_id, public_ref, view_revision, created_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT (session_id, document_id) DO NOTHING
            """,
            (session_id, document["id"], crypto.public_ref("doc"), time.time()),
        )
        session_document = handle.execute(
            "SELECT * FROM session_documents WHERE session_id = ? AND document_id = ?",
            (session_id, document["id"]),
        ).fetchone()

        for field in handle.execute(
            "SELECT id FROM redaction_fields WHERE document_id = ? ORDER BY start_offset",
            (document["id"],),
        ).fetchall():
            handle.execute(
                """
                INSERT INTO session_fields (session_document_id, redaction_field_id, public_ref, override, version)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT (session_document_id, redaction_field_id) DO NOTHING
                """,
                (session_document["id"], field["id"], crypto.public_ref("fld"), str(Override.NONE)),
            )
    return session_document


def get_session_document(handle: sqlite3.Connection, *, session_id: int, slug: str) -> sqlite3.Row | None:
    return handle.execute(
        """
        SELECT session_documents.*, documents.slug, documents.title
        FROM session_documents
        JOIN documents ON documents.id = session_documents.document_id
        WHERE session_documents.session_id = ? AND documents.slug = ?
        """,
        (session_id, slug),
    ).fetchone()


def list_fields(handle: sqlite3.Connection, *, session_document_id: int) -> list[FieldView]:
    rows = handle.execute(
        """
        SELECT session_fields.id AS session_field_id,
               session_fields.public_ref,
               session_fields.override,
               session_fields.version,
               redaction_fields.start_offset,
               redaction_fields.end_offset,
               redaction_fields.category,
               redaction_fields.base_entity_type,
               redaction_fields.confidence,
               redaction_fields.safe_context_hint
        FROM session_fields
        JOIN redaction_fields ON redaction_fields.id = session_fields.redaction_field_id
        WHERE session_fields.session_document_id = ?
        ORDER BY redaction_fields.start_offset
        """,
        (session_document_id,),
    ).fetchall()
    return [
        FieldView(
            session_field_id=int(row["session_field_id"]),
            field_ref=row["public_ref"],
            start_offset=int(row["start_offset"]),
            end_offset=int(row["end_offset"]),
            category=row["category"],
            base_entity_type=row["base_entity_type"],
            confidence=float(row["confidence"]),
            safe_context_hint=row["safe_context_hint"],
            override=row["override"],
            version=int(row["version"]),
        )
        for row in rows
    ]


def unmapped_field_count(handle: sqlite3.Connection, *, session_document_id: int) -> int:
    """Detected fields this session holds no reference for.

    Rendering derives masking solely from `session_fields`, so an incomplete
    mapping does not fail loudly — it emits the raw span for everything missing.
    A non-zero count here means a view must be refused rather than built.
    """
    row = handle.execute(
        """
        SELECT (
                   SELECT COUNT(*) FROM redaction_fields
                   WHERE redaction_fields.document_id = session_documents.document_id
               ) - (
                   SELECT COUNT(*) FROM session_fields
                   WHERE session_fields.session_document_id = session_documents.id
               ) AS missing
        FROM session_documents
        WHERE session_documents.id = ?
        """,
        (session_document_id,),
    ).fetchone()
    return 0 if row is None else int(row["missing"])


def get_session_field(handle: sqlite3.Connection, *, session_id: int, field_ref: str) -> sqlite3.Row | None:
    """Resolve a public field reference, always joined through the caller's session.

    The session predicate is not an optimisation. Without it, a leaked reference
    from another browser would resolve here.
    """
    return handle.execute(
        """
        SELECT session_fields.id AS session_field_id,
               session_fields.public_ref AS field_ref,
               session_fields.override,
               session_fields.version,
               session_fields.session_document_id,
               session_documents.session_id,
               session_documents.view_revision,
               redaction_fields.id AS redaction_field_id,
               redaction_fields.start_offset,
               redaction_fields.end_offset,
               redaction_fields.category,
               redaction_fields.base_entity_type,
               redaction_fields.confidence,
               redaction_fields.safe_context_hint,
               redaction_fields.canonical_hmac,
               documents.content_sha256,
               documents.slug AS document_slug
        FROM session_fields
        JOIN session_documents ON session_documents.id = session_fields.session_document_id
        JOIN redaction_fields ON redaction_fields.id = session_fields.redaction_field_id
        JOIN documents ON documents.id = session_documents.document_id
        WHERE session_fields.public_ref = ?
          AND session_documents.session_id = ?
        """,
        (field_ref, session_id),
    ).fetchone()


def document_text(handle: sqlite3.Connection, *, session_document_id: int) -> str:
    row = handle.execute(
        """
        SELECT documents.slug, documents.encrypted_text, documents.nonce
        FROM session_documents
        JOIN documents ON documents.id = session_documents.document_id
        WHERE session_documents.id = ?
        """,
        (session_document_id,),
    ).fetchone()
    if row is None:
        raise LookupError("session document not found")
    return crypto.decrypt_document(row["slug"], row["encrypted_text"], row["nonce"])


def resolve_raw_value(handle: sqlite3.Connection, *, session_field_id: int) -> str:
    """Decrypt and slice one field. Callers must already hold a consumed grant."""
    row = handle.execute(
        """
        SELECT documents.slug,
               documents.encrypted_text,
               documents.nonce,
               redaction_fields.start_offset,
               redaction_fields.end_offset
        FROM session_fields
        JOIN session_documents ON session_documents.id = session_fields.session_document_id
        JOIN documents ON documents.id = session_documents.document_id
        JOIN redaction_fields ON redaction_fields.id = session_fields.redaction_field_id
        WHERE session_fields.id = ?
        """,
        (session_field_id,),
    ).fetchone()
    if row is None:
        raise LookupError("session field not found")
    text = crypto.decrypt_document(row["slug"], row["encrypted_text"], row["nonce"])
    return text[int(row["start_offset"]) : int(row["end_offset"])]


def _already_ingested(handle: sqlite3.Connection, *, slug: str, text: str) -> bool:
    """True when the stored copy of this document is already current.

    Re-ingesting is destructive: it deletes and recreates `redaction_fields`, and
    `session_fields` cascades from those rows. Doing that on every boot strips the
    per-session mapping out from under every live session, and a session that
    resolves no fields renders the document with nothing masked at all. Seeding
    therefore has to be a no-op when the content has not changed.
    """
    row = handle.execute(
        "SELECT processing_status, content_sha256 FROM documents WHERE slug = ?",
        (slug,),
    ).fetchone()
    if row is None:
        return False
    # A FAILED document is re-ingested deliberately, so a boot with a working
    # detector retries one that was stored while the detector was unavailable.
    return (
        row["processing_status"] == ProcessingStatus.READY
        and row["content_sha256"] == crypto.content_digest(text)
    )


def seed_from_fixtures(handle: sqlite3.Connection, directory) -> list[str]:
    slugs: list[str] = []
    for path in sorted(directory.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        slugs.append(path.stem)
        if _already_ingested(handle, slug=path.stem, text=text):
            continue
        title = text.strip().splitlines()[0][:120] if text.strip() else path.stem
        ingest_document(handle, slug=path.stem, title=title, text=text)
    return slugs


def record_view_event(
    handle: sqlite3.Connection,
    *,
    session_id: int,
    session_document_id: int,
    purpose: str,
    page_index: int,
    has_more: bool,
    masked_count: int,
    visible_count: int,
    view_revision: int,
) -> None:
    with immediate(handle):
        audit.record(
            handle,
            actor=Actor.AGENT,
            event_type=EventType.DOCUMENT_VIEWED,
            outcome="ok",
            session_id=session_id,
            session_document_id=session_document_id,
            reason=purpose,
            metadata={
                "page_index": page_index,
                "has_more": has_more,
                "masked_field_count": masked_count,
                "visible_field_count": visible_count,
                "view_revision": view_revision,
            },
        )
