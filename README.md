# Redactly

A document viewer that exposes real WebMCP tools to a browser AI agent without
letting a sensitive value reach the DOM, browser storage, a log line, or an
agent's context by accident.

Redaction tools assume a human is the only reader. Once an agent can call
`verify_field` or `read_document` on the page, redaction stops being a UI concern
and becomes a security boundary problem. Redactly is an answer to one question:
can a viewer hand an agent genuinely useful capabilities over a sensitive
document — read it, check a value, ask for a reveal, argue a redaction is wrong,
read the audit trail — while keeping every raw value on the server side of a
policy service?

Everything in `fixtures/synthetic/` is synthetic. No real personal data is used
anywhere in this repository.

## The five tools

All five are registered on the document viewer route only, and are torn down via
`AbortSignal` when it unmounts.

| Tool | What it returns | What it never returns |
| --- | --- | --- |
| `request_document_view` | One page of masked text plus placeholder ids | Any sensitive value, any model confidence score |
| `verify_without_reveal` | Whether a value you already hold matches a field | The field's value; the guess is never stored |
| `request_field_unmask` | One raw value, once, after a human approves | A second copy — a replay returns `already_consumed` |
| `challenge_redaction` | A decision and the classifier's assessment | The value; the detector's global output is not rewritten |
| `get_redaction_audit_log` | Cursor-paginated outcomes, actors, categories | Values, comparison guesses, session tokens |

## What the design actually guarantees

- **At most once.** A reveal is a server-side event with no undo. Once a value is
  in an agent's context it may already be in a provider's logs or a downstream
  tool call, so revocation is meaningless and the only enforceable property is
  that the second caller gets nothing. Grants are consumed inside a
  `BEGIN IMMEDIATE` transaction with a conditional
  `UPDATE ... WHERE state = 'READY'`; under 50 concurrent consumers exactly one
  response carries the value.
- **No resolvable map.** There is no `placeholder_id -> value` table. Documents
  are stored AES-GCM encrypted and a field is only resolvable by decrypting
  inside the API process and slicing by character offset.
- **Equality without an oracle.** `verify_without_reveal` compares a keyed
  HMAC of a canonicalised value in constant time, with a per-field domain
  separator so two documents naming the same person do not share a digest. It is
  rate limited per field and per session, because an unbounded equality check is
  a brute-force search.
- **Untrusted document text.** Document bodies can contain instructions aimed at
  the reading agent, so `request_document_view` is annotated
  `untrustedContentHint: true`. `fixtures/synthetic/incident-report-untrusted.txt`
  contains such a block; following it changes nothing, because an agent has no
  capability to approve its own request.
- **Nothing succeeds unlogged.** Every state change and its audit row commit in
  the same transaction. Audit metadata is key-allowlisted and scalar-only.
- **Session-scoped references.** Placeholder ids are minted per browser session
  and every lookup joins through the session derived from an HttpOnly cookie. A
  reference from another session is indistinguishable from one that never existed.

### What it does not guarantee

Approval happens in a modal on the same page an agent can drive, so a
browser-resident agent could observe the queue or race the click. Production
would move approval to an authenticated out-of-band channel. There is also no
login: the session cookie isolates viewers from each other but does not establish
document ownership.

## Layout

```text
apps/web/           Next.js App Router viewer; the only place tools are registered
services/api/       FastAPI policy service; owns raw text, detection, state machines
fixtures/synthetic/ Synthetic documents seeded on startup
model-spike/        Reproducible ONNX detector evaluation that preceded the build
docs/               Architecture proposal, demo script, submission copy
```

Between runs, `uv run python scripts/reset_demo.py` drops the database so the
fixtures are re-scanned and the audit trail starts empty. The service has to be
stopped first, since SQLite holds the file open.

## Deploying

```bash
cp .env.example .env      # optional; pick a free REDACTLY_HOST_PORT
docker compose up -d --build
```

Listens on `127.0.0.1:28419` by default, for a reverse proxy to terminate TLS in
front of. One container, one origin: Next.js is the front door and uvicorn
listens on container loopback. That is a requirement rather than tidiness — an unmask blocks
server-side until a human decides, so anything with a short request timeout in
front of it will cut the approval off mid-wait. Put TLS in front, since WebMCP
needs a secure context to activate at all.

Full instructions, including the reverse-proxy timeout that most often breaks a
deploy and how to get native WebMCP via a Chrome origin-trial token, are in
[`docs/deploy.md`](docs/deploy.md).

## Running it

Two terminals. Python 3.11+ with [uv](https://docs.astral.sh/uv/), and Node 20+.

```bash
# Terminal 1 — policy service on :8000
cd services/api
uv sync --extra dev
uv run uvicorn app.main:app --reload --port 8000
```

```bash
# Terminal 2 — viewer on :3000
cd apps/web
npm install
npm run dev
```

Open <http://localhost:3000>. The viewer proxies the API under its own origin at
`/api/backend/*` so the session cookie stays `SameSite=Strict`.

The first backend start downloads the ONNX detector
(`gravitee-io/bert-small-pii-detection`, Apache-2.0). To run without the model:

```bash
REDACTLY_DETECTOR=deterministic uv run uvicorn app.main:app --port 8000
```

WebMCP needs a secure context; `localhost` counts. In a browser without
`document.modelContext`, the in-page agent console invokes the same handlers the
tools wrap, so the flow is still demonstrable — the boundary is in the policy
service either way.

### Walking the whole lifecycle without a browser

`scripts/smoke_demo.py` plays both roles against a running service: it calls
each tool as the agent and answers the prompts as the owner, printing every
response. It is the fastest way to confirm an install, and it doubles as a
narration script.

```bash
cd services/api
uv run python scripts/smoke_demo.py
uv run python scripts/smoke_demo.py --base-url http://localhost:3000/api/backend
```

The second form goes through the viewer's rewrite, which exercises the cookie
path the browser actually uses. On the claim fixture the detector labels the
claim reference `999-88-7777` as an SSN with 0.95 confidence — a real
false positive — so the challenge step argues an honest case rather than a
manufactured one.

## Tests

```bash
cd services/api
uv run pytest -v
```

| File | Property under test |
| --- | --- |
| `test_atomic_reveal.py` | 50 concurrent consumers, exactly one value; replay is `already_consumed` |
| `test_no_raw_leaks.py` | A unique sentinel appears in no response, audit row, database column, or log line other than the one reveal |
| `test_session_isolation.py` | Cross-session references are rejected and indistinguishable from unknown ones |
| `test_challenge_scope.py` | An approved challenge changes one session, revokes live grants, and invalidates stale cursors |
| `test_timeouts.py` | Unanswered requests and expired grants fail closed; an agent is never the actor that resolves its own request |
| `test_verification_oracle.py` | Rate limits bound enumeration; digests do not correlate across documents |
| `test_state_machines.py` | No transition returns to `PENDING` or `READY` |

## Configuration

Every setting is read from the environment with a `REDACTLY_` prefix; see
`services/api/app/config.py`. The ones that matter for a demo:

| Variable | Default | Purpose |
| --- | --- | --- |
| `REDACTLY_MASTER_KEY` | generated into `services/api/var/` | Base64 32-byte HKDF root for document encryption, digests, and cursors |
| `REDACTLY_DETECTOR` | `onnx` | `deterministic` runs pattern-based detection with no model download |
| `REDACTLY_APPROVAL_TIMEOUT_SECONDS` | `120` | How long a tool call waits for a human |
| `REDACTLY_GRANT_TTL_SECONDS` | `60` | How long an approval stays spendable |
| `REDACTLY_VERIFY_MAX_PER_FIELD` | `5` | Verification attempts per field per window |

Pin the detector artefact for reproducibility:

```bash
cd services/api
uv run python scripts/write_model_manifest.py   # writes models/model-manifest.json
```
