# Devpost submission copy

Paste-ready text for the WebMCP Challenge submission. Every number and claim
below was checked against a real run of the built system, not estimated.

---

## Project Story (paste into "About the project")

## Inspiration

WebMCP lets browser-based AI agents call tools directly on the page a user is
looking at, which is powerful but also a little terrifying when that page
contains sensitive documents. Redaction tools today assume a human is the only
reader. The moment an agent can call `verify_field` or `read_document`, redaction
stops being a UI concern and becomes a security boundary problem: what happens
when the "user" reading your masked document is an LLM that can be prompted,
jailbroken, or simply mistaken?

We built Redactly to answer that question directly: can a document viewer expose
real WebMCP tools to an agent — verify, request unmask, challenge a redaction —
without ever letting a raw sensitive value leak into the DOM, browser storage,
logs, or an agent's context by accident?

## What it does

Redactly is a document viewer that exposes five WebMCP tools bound to the
currently open document:

- **`request_document_view`** returns the masked document, paginated by cursor.
- **`verify_without_reveal`** lets an agent check whether a value it already
  holds matches a redacted field *without ever returning the field itself*
  (rate limited, so it can't be used as a brute-force oracle).
- **`request_field_unmask`** asks a human owner to approve revealing a specific
  field. Approval creates a grant that can be consumed **exactly once**; a
  second attempt returns `already_consumed`, not the value.
- **`challenge_redaction`** lets an agent argue a field was misclassified. A
  human approval flips the field to non-sensitive for that session only — it
  never rewrites the underlying model's global output.
- **`get_redaction_audit_log`** is a read-only, cursor-paginated trail of every
  view, verification, unmask, and challenge event.

The challenge tool isn't hypothetical, and we didn't have to manufacture a case
for it. On our insurance claim fixture, the detector labels the line
`Claim reference: 999-88-7777` as a Social Security number with 0.95
confidence. It is a claim reference that happens to share the 3-2-4 digit shape
of an SSN. A high-confidence false positive is exactly the situation where an
agent has something useful to say and no legitimate way to say it, so
`challenge_redaction` gives it one: state the argument, let a human rule on it,
and unmask that single field for that one session.

Every reveal is a **server-side, at-most-once event**: once a value is returned
to a tool call, Redactly has no way to un-send it, so the whole system is
designed around never handing it out casually. A same-page approval modal drives
the human side of the demo; in a production deployment that approval channel
would move out-of-band, since a browser agent could otherwise observe the same
page the human is approving on.

## How we built it

- **Frontend:** Next.js App Router, with the document viewer as the sole route
  the WebMCP tools are registered against. Tools are torn down via `AbortSignal`
  on unmount, and pending fetches are aborted rather than left running.
- **Backend:** A FastAPI policy service that owns all raw text, ONNX-based PII
  detection, and the state machines for reveals and challenges. The detector's
  raw inference route is internal only and deliberately never exposed as a tool.
- **Detection:** `gravitee-io/bert-small-pii-detection` (Apache-2.0) run through
  ONNX Runtime, pinned by revision and artefact SHA-256 in a committed manifest
  so the demo is reproducible. Token predictions are aggregated into character
  spans, trimmed, merged, and de-overlapped before anything becomes a field.
- **Data model:** Documents are stored AES-GCM encrypted. Redacted fields are
  referenced by random per-session `public_ref` values, never a real
  placeholder-to-value map, so nothing outside the trusted API process can
  resolve a field back to its text. Equality checks for `verify_without_reveal`
  go through a canonical, domain-separated HMAC compared in constant time, so
  the endpoint can't be turned into a brute-force oracle.
- **Reveal consumption:** Grants are consumed inside a `BEGIN IMMEDIATE`
  transaction with a conditional `UPDATE ... WHERE state = 'READY'`, so under
  concurrent requests exactly one caller gets the value and every other caller
  gets `already_consumed` — verified with a 50-way concurrency test against a
  synthetic sentinel value.
- **Audit trail:** Every state mutation and its audit event commit in the same
  transaction, so there's no window where an action succeeds but goes unlogged.
  Audit metadata is key-allowlisted and scalar-only, so raw values, verification
  guesses, and session tokens structurally cannot end up there.

The invariants are covered by 39 tests, including the concurrency test above and
a sentinel sweep that plants a unique value in a document and then asserts it
appears in no response body, no audit row, no database column, and no log line
other than the single authorised reveal.

## Challenges we ran into

The core challenge was designing for an adversarial reader that isn't a browser
— it's a language model that might be prompted (by us, by a compromised page, or
by the document text itself) to try to exfiltrate a value. That reshaped almost
every decision:

- Treating **document text itself** as untrusted input, since it can contain
  adversarial instructions aimed at the agent reading it. One of our fixtures
  contains exactly such an injection block; following it changes nothing,
  because an agent has no capability to approve its own request.
- Making sure a "read-only" tool actually *is* read-only. `verify_without_reveal`
  looks harmless but is a textbook equality oracle if not rate-limited and
  constant-time.
- Deciding that unmask has to be **at-most-once**, not just access-controlled,
  because once an agent has a value in its context, revocation is meaningless.
- Keeping model confidence scores out of the normal masked view entirely, while
  still surfacing them during a challenge review, so the classifier's internals
  aren't leaking through the "harmless" viewing path.
- Domain-separating field digests per document, so two documents naming the same
  person don't produce a matching HMAC that an agent could use to correlate them.

## What we learned

Building for an AI agent caller changes the security model even when the
underlying stack (Next.js, FastAPI, SQLite) is completely ordinary. The
interesting design work isn't the CRUD — it's things like proving a reveal grant
is consumed exactly once under concurrency, or writing an invariant test that
sweeps every non-reveal response, log line, and audit row for a leaked sentinel
value. WebMCP makes it trivially easy to hand an agent real capabilities on your
page; it does not make it easy to reason about what an agent will do with them,
and that gap is where most of our design time went.

We also learned that the honest failure modes are worth stating. Our approval
modal lives on the same page an agent can drive, so a browser-resident agent
could observe the queue or race the click. That's a real limitation of the demo,
not something a better modal fixes — it needs a different channel.

## What's next for Redactly

- Move human approval to a genuinely out-of-band channel (push notification or a
  separate authenticated device) so a browser-resident agent can't observe or
  race the approval UI.
- Add real user authentication and document ownership, replacing the
  session-cookie isolation used for the hackathon demo.
- Expand the canonicalization rules for `verify_without_reveal` to more field
  types (dates, phone formats, addresses) beyond the current type-aware set.
- Publish the concurrency and no-raw-leak invariant tests as a reusable suite
  other WebMCP tool builders can adapt.

---

## Built with (paste into the tags field)

```text
nextjs, fastapi, python, typescript, onnx, onnxruntime, sqlite, webmcp, mcp,
pydantic, app-router, ai-agents, pii-detection, aes-gcm, huggingface
```

That is 15 of the allowed 25. `onnxruntime`, `aes-gcm`, and `huggingface` were
added because all three are load-bearing and searchable.

---

## Still needed before submitting

| Field | Status |
| --- | --- |
| Project Story | Ready — copy the section above |
| Built with | Ready — copy the tag list above |
| "Try it out" link | Needs a pushed repo URL |
| Video demo link | Needs a recorded video (see `docs/demo-script.md`) |

The "Please enter 1 or more character" validation error is the empty required
video demo link, not the story.

---

## Claims a judge could check, and where they hold up

| Claim in the story | Where it is verifiable |
| --- | --- |
| Exactly one of many concurrent consumers gets a value | `services/api/tests/test_atomic_reveal.py` |
| A sentinel value never leaks anywhere but the one reveal | `services/api/tests/test_no_raw_leaks.py` |
| A challenge changes one session, not global output | `services/api/tests/test_challenge_scope.py` |
| Verification cannot be used as a brute-force oracle | `services/api/tests/test_verification_oracle.py` |
| Unanswered requests fail closed | `services/api/tests/test_timeouts.py` |
| The detector artefact is pinned | `services/api/models/model-manifest.json` |
| The 999-88-7777 false positive is real | `uv run python scripts/smoke_demo.py` |
