-- Redactly persistence model.
--
-- There is deliberately no `placeholder_id -> plaintext` table. A redacted field
-- is only resolvable by decrypting `documents.encrypted_text` inside the trusted
-- API process and slicing it with `redaction_fields` offsets. Everything the
-- browser or an agent ever sees is a random per-session `public_ref`.
--
-- All `*_at` columns are epoch seconds (REAL) so lifetime comparisons happen in
-- SQL without any string-format assumptions.

CREATE TABLE IF NOT EXISTS documents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT    NOT NULL UNIQUE,
    title             TEXT    NOT NULL,
    encrypted_text    BLOB    NOT NULL,
    nonce             BLOB    NOT NULL,
    content_sha256    TEXT    NOT NULL,
    processing_status TEXT    NOT NULL CHECK (processing_status IN ('PENDING', 'READY', 'FAILED')),
    model_name        TEXT,
    model_revision    TEXT,
    onnx_sha256       TEXT,
    created_at        REAL    NOT NULL,
    updated_at        REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS redaction_fields (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id              INTEGER NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    start_offset             INTEGER NOT NULL,
    end_offset               INTEGER NOT NULL,
    base_entity_type         TEXT    NOT NULL,
    category                 TEXT    NOT NULL,
    confidence               REAL    NOT NULL,
    canonical_hmac           BLOB    NOT NULL,
    canonicalization_version INTEGER NOT NULL,
    safe_context_hint        TEXT    NOT NULL,
    CHECK (end_offset > start_offset),
    UNIQUE (document_id, start_offset, end_offset)
);

CREATE INDEX IF NOT EXISTS idx_redaction_fields_document
    ON redaction_fields (document_id, start_offset);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hmac   TEXT    NOT NULL UNIQUE,
    status       TEXT    NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED')),
    created_at   REAL    NOT NULL,
    expires_at   REAL    NOT NULL,
    last_seen_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS session_documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    document_id   INTEGER NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    public_ref    TEXT    NOT NULL UNIQUE,
    view_revision INTEGER NOT NULL DEFAULT 1,
    created_at    REAL    NOT NULL,
    UNIQUE (session_id, document_id)
);

CREATE TABLE IF NOT EXISTS session_fields (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_document_id INTEGER NOT NULL REFERENCES session_documents (id) ON DELETE CASCADE,
    redaction_field_id  INTEGER NOT NULL REFERENCES redaction_fields (id) ON DELETE CASCADE,
    public_ref          TEXT    NOT NULL UNIQUE,
    override            TEXT    NOT NULL DEFAULT 'NONE' CHECK (override IN ('NONE', 'NON_SENSITIVE')),
    version             INTEGER NOT NULL DEFAULT 1,
    UNIQUE (session_document_id, redaction_field_id)
);

-- The guess supplied to verify_without_reveal is never a column here. Only the
-- boolean outcome is retained, which is what the rate limiter needs.
CREATE TABLE IF NOT EXISTS verification_attempts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_field_id INTEGER NOT NULL REFERENCES session_fields (id) ON DELETE CASCADE,
    matches          INTEGER NOT NULL CHECK (matches IN (0, 1)),
    purpose          TEXT    NOT NULL,
    created_at       REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_verification_attempts_field_time
    ON verification_attempts (session_field_id, created_at);

CREATE TABLE IF NOT EXISTS approval_requests (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    public_ref          TEXT    NOT NULL UNIQUE,
    kind                TEXT    NOT NULL CHECK (kind IN ('UNMASK', 'CHALLENGE')),
    session_document_id INTEGER NOT NULL REFERENCES session_documents (id) ON DELETE CASCADE,
    session_field_id    INTEGER NOT NULL REFERENCES session_fields (id) ON DELETE CASCADE,
    status              TEXT    NOT NULL CHECK (
                            status IN ('PENDING', 'APPROVED', 'DENIED', 'TIMED_OUT', 'CANCELLED', 'STALE')
                        ),
    reason              TEXT    NOT NULL,
    field_version       INTEGER NOT NULL,
    requested_at        REAL    NOT NULL,
    expires_at          REAL    NOT NULL,
    resolved_at         REAL,
    human_note          TEXT
);

CREATE INDEX IF NOT EXISTS idx_approval_requests_pending
    ON approval_requests (session_document_id, status, requested_at);

-- `receipt_ref` is minted at issue time and returned beside the one raw
-- response. It is a correlation handle for the audit log, never a bearer token:
-- presenting it after consumption yields `already_consumed`, not the value.
CREATE TABLE IF NOT EXISTS reveal_grants (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       INTEGER NOT NULL UNIQUE REFERENCES approval_requests (id) ON DELETE CASCADE,
    session_field_id INTEGER NOT NULL REFERENCES session_fields (id) ON DELETE CASCADE,
    receipt_ref      TEXT    NOT NULL UNIQUE,
    state            TEXT    NOT NULL CHECK (state IN ('READY', 'CONSUMED', 'EXPIRED', 'REVOKED')),
    field_version    INTEGER NOT NULL,
    issued_at        REAL    NOT NULL,
    expires_at       REAL    NOT NULL,
    consumed_at      REAL
);

-- AUTOINCREMENT (rather than plain rowid) guarantees ids are monotonic and never
-- reused, so an opaque cursor over `id` cannot silently skip or replay events.
CREATE TABLE IF NOT EXISTS audit_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid                TEXT    NOT NULL UNIQUE,
    session_id          INTEGER REFERENCES sessions (id) ON DELETE CASCADE,
    session_document_id INTEGER REFERENCES session_documents (id) ON DELETE CASCADE,
    session_field_id    INTEGER REFERENCES session_fields (id) ON DELETE CASCADE,
    actor               TEXT    NOT NULL CHECK (actor IN ('AGENT', 'HUMAN', 'SYSTEM')),
    event_type          TEXT    NOT NULL,
    outcome             TEXT    NOT NULL,
    reason              TEXT,
    metadata            TEXT    NOT NULL DEFAULT '{}',
    occurred_at         REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_document
    ON audit_events (session_document_id, id);
