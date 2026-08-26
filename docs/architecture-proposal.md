# Redactly MVP architecture proposal

Status: proposed after the model spike. This document defines the security
boundary and data model before the application scaffold is built.

## Decisions

- Use a Next.js App Router frontend and one FastAPI policy service.
- Run ONNX inference inside FastAPI. A raw-document detection route, if kept for
  diagnostics, is internal-only and never exposed as a WebMCP tool.
- Bind the five WebMCP tools to the active document-viewer route. Do not accept a
  caller-supplied `document_id` when the current page already establishes scope.
- Keep original text, raw field values, verification guesses, and reveal grants
  out of the DOM, React state, browser storage, analytics, error bodies, and logs.
- Treat agent arguments, agent reasons, and document text as untrusted input.
- Describe unmasking as an **at-most-once server-side reveal**. Once a value is
  returned, Redactly cannot erase it from an agent context or downstream system.
- A same-page approval modal is acceptable for the cooperative demo. A production
  owner approval channel must be authenticated and out-of-band because a browser
  agent may be able to inspect the visible page.

## Monorepo

```text
redactly/
├─ apps/
│  └─ web/
│     ├─ app/documents/[documentRef]/page.tsx
│     ├─ components/
│     │  ├─ MaskedDocument.tsx
│     │  ├─ ApprovalModal.tsx
│     │  ├─ ChallengeModal.tsx
│     │  └─ AuditTable.tsx
│     └─ lib/
│        ├─ api/client.ts
│        └─ webmcp/
│           ├─ register-tools.ts
│           ├─ tool-schemas.ts
│           └─ safe-tool-result.ts
├─ services/
│  └─ api/
│     ├─ app/
│     │  ├─ api/routes/
│     │  │  ├─ sessions.py
│     │  │  ├─ agent_tools.py
│     │  │  ├─ owner_decisions.py
│     │  │  └─ pending_events.py
│     │  ├─ domain/{enums.py,policies.py,state_machines.py}
│     │  ├─ services/{redaction.py,verification.py,approvals.py,challenges.py,audit.py}
│     │  ├─ inference/{onnx_detector.py,label_mapping.py,span_resolution.py}
│     │  ├─ security/{crypto.py,sessions.py,log_filters.py}
│     │  └─ db/{models.py,session.py}
│     ├─ migrations/
│     └─ tests/
│        ├─ test_atomic_reveal.py
│        ├─ test_session_isolation.py
│        ├─ test_timeouts.py
│        ├─ test_challenge_scope.py
│        └─ test_no_raw_leaks.py
├─ packages/contracts/           # OpenAPI-generated TypeScript types
├─ fixtures/synthetic/
├─ models/model-manifest.json    # model id, revision, license, ONNX SHA-256
├─ model-spike/
├─ compose.yaml
└─ README.md
```

Use a same-origin `/api/backend/*` reverse proxy from Next.js to FastAPI. Session
identity belongs in a high-entropy `Secure; HttpOnly; SameSite=Strict` cookie,
not a tool argument. With login deliberately out of scope, this isolates judge
sessions but does not establish real document ownership.

## Persistence model

The logical placeholder map is implemented by `redaction_fields` plus
`session_fields`. It is not a plaintext `placeholder_id -> real_value` table.

| Table | Required fields and constraints |
| --- | --- |
| `documents` | `id`, `slug`, `title`, `encrypted_text`, `nonce`, `content_sha256`, `processing_status`, `model_name`, `model_revision`, `onnx_sha256`, timestamps |
| `redaction_fields` | `id`, `document_id`, character offsets, `base_entity_type`, `category`, `confidence`, `canonical_hmac`, `canonicalization_version`, `safe_context_hint`; unique span per document |
| `sessions` | `id`, `token_hmac`, `status`, `created_at`, `expires_at`, `last_seen_at` |
| `session_documents` | `id`, `session_id`, `document_id`, random `public_ref`, `view_revision`; unique `(session_id, document_id)` |
| `session_fields` | `id`, `session_document_id`, `redaction_field_id`, random `public_ref`, `override`, `version`; unique public ref per session |
| `verification_attempts` | `id`, `session_field_id`, `matches`, `purpose`, `created_at`; never persist the comparison guess |
| `approval_requests` | `id`, random `public_ref`, `kind`, scoped document/field IDs, `status`, `reason`, `field_version`, request/expiry/resolution timestamps, optional human note |
| `reveal_grants` | `id`, `request_id UNIQUE`, `session_field_id`, `state`, `field_version`, issued/expiry/consumed timestamps |
| `audit_events` | monotonic `id`, UUID, scoped IDs, `actor`, `event_type`, `outcome`, `reason`, allowlisted JSON metadata, `occurred_at` |

Encrypt source documents with AES-GCM. Resolve a field by offsets only inside the
trusted API process. Store a canonical HMAC for equality checks with a per-field
domain separator so equal values cannot be correlated across documents. Use
type-aware canonicalization and constant-time digest comparison. Rate-limit
verification attempts per session and field; `verify_without_reveal` is otherwise
an equality oracle.

Every public lookup must include the current server-derived session:

```sql
WHERE session_fields.public_ref = :field_ref
  AND session_documents.session_id = :current_session
```

## Unmask state machine

```text
PENDING
  ├─ human deny ───────────────> DENIED
  ├─ deadline reached ─────────> TIMED_OUT
  ├─ tool/browser abort ───────> CANCELLED
  ├─ field/session changed ────> STALE
  └─ human approve ────────────> APPROVED + grant READY

grant READY
  ├─ deadline reached ─────────> EXPIRED
  ├─ field version changed ────> REVOKED
  └─ first result retrieval ───> CONSUMED + one raw response
```

Consume in a SQLite `BEGIN IMMEDIATE` transaction using a conditional update:

```sql
UPDATE reveal_grants
SET state = 'CONSUMED', consumed_at = :now
WHERE id = :grant_id
  AND state = 'READY'
  AND expires_at > :now
  AND field_version = :current_field_version
RETURNING session_field_id;
```

Exactly one concurrent request receives a row. Append `REVEAL_CONSUMED` in the
same transaction and commit before decrypting the field. If delivery fails after
commit, the secret is lost rather than replayable. Return an already-consumed
`reveal_receipt_id` beside the one raw response; a replay proves that the receipt
cannot fetch the value. A fresh tool invocation requires fresh human approval.

## Challenge state machine

```text
PENDING
  ├─ reject / timeout / cancel / stale ──> mask unchanged
  └─ approve ────────────────────────────> session override NON_SENSITIVE
                                           field version + 1
                                           view revision + 1
```

Apply approval and the override atomically only when the field version still
matches the request snapshot. Scope the override to `session_fields`; do not
rewrite the detector's global result. Revoke stale reveal grants and conflicting
pending requests. `challenge_redaction` returns the decision and new view revision,
not the raw value. A subsequent `request_document_view` exposes the newly
non-sensitive field.

## Audit invariants

State mutation and its audit event commit in the same transaction. Audit failure
fails the operation. Events include document view, verification, unmask request
and resolution, reveal consumption, challenge request and resolution, and field
reclassification. Actors are `AGENT`, `HUMAN`, or `SYSTEM`.

Never store raw values, verification guesses, session cookies, bearer values, or
encrypted blobs in audit metadata. Paginate audit output with an opaque cursor and
small limit. HTTP can prove `REVEAL_CONSUMED`, not that the agent received it.

## WebMCP contract corrections

Recommended inputs:

```text
request_document_view({ purpose, cursor? })
verify_without_reveal({ placeholder_id, comparison_value, purpose })
request_field_unmask({ placeholder_id, reason })
challenge_redaction({ placeholder_id, reasoning })
get_redaction_audit_log({ purpose, cursor?, limit? })
```

- Use `additionalProperties: false`, bounded strings, descriptive titles, and tool
  annotations. Mark only view and audit retrieval as read-only.
- Register on the viewer route, abort pending fetch/poll work on unmount, and pass
  the WebMCP execution signal through to `fetch`.
- Validate every argument again with Pydantic; browser schemas are not a security
  boundary.
- Return small JSON-serializable discriminated results. Do not leak backend errors.
- Do not expose confidence in the normal masked view. A challenge response may
  include the model type/confidence that informed the human review.
- Fail closed on model errors, pending timeouts, stale versions, and unavailable
  approval channels.

Recommended terminal results:

```text
unmask:
  { status: "revealed", value, reveal_receipt_id, consumed: true }
  { status: "denied" | "timed_out" | "cancelled" | "stale" | "already_consumed" }

challenge:
  { status: "approved", view_revision, model_assessment }
  { status: "rejected" | "timed_out" | "cancelled" | "stale", model_assessment }
```

The first backend tests should run 50 concurrent consume requests and assert that
one response contains a unique synthetic sentinel while the other 49 return
`already_consumed`. Separate invariant tests should search every non-reveal
response, error, audit row, structured log, and browser payload for the sentinel.

