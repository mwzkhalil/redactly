# Demo video script

A shot list for the required Devpost video. Timed to about 2:40, which leaves
room to breathe inside a 3:00 limit. Every value quoted below is what the system
actually returns, so nothing here needs faking.

## Before you record

```bash
# Terminal 1 — policy service
cd services/api
uv run uvicorn app.main:app --port 8000

# Terminal 2 — viewer
cd apps/web
npm run dev
```

Then reset the demo to a clean state so the audit trail starts empty:

```bash
cd services/api
uv run python scripts/reset_demo.py
```

Checklist:

- Browser at `http://localhost:3000`, window sized so the masked document and
  the agent console are both visible without scrolling.
- Zoom to about 125%. The placeholder ids and JSON responses have to be legible
  after compression.
- Close other tabs. The header pill reads either
  `document.modelContext detected` or `WebMCP not available in this browser`,
  and whichever it says will be on camera.
- Do a silent dry run first. `request_field_unmask` blocks until you click, and
  fumbling for the modal on camera reads as a bug rather than as the design.
- After the dry run, reset again — the dry run leaves its own audit trail.

Clicking a placeholder in the document targets it in the agent console. That is
the only reliable way to pick between two fields the detector gave the same
category, which this fixture has.

## The shots

### 1. The problem, on the document itself (0:00–0:25)

Open `Northstar Mutual — Household Claim 4471`.

> "This is an insurance claim. The policyholder's name, date of birth, address,
> email, phone, Social Security number, and bank account are all redacted — and
> the page is exposing five WebMCP tools to any agent in the browser. So the
> question isn't whether a human can read this. It's what an agent can get."

Point at the header: masked field count, and `5 tools registered`.

### 2. Reading is allowed, and it's genuinely useful (0:25–0:45)

Agent console, tool `request_document_view`, purpose left as-is. Call it.

> "The agent gets the document, paginated, with every sensitive field replaced by
> a session-scoped placeholder id. It gets the structure and the prose — enough
> to actually do work — and not one sensitive value."

Scroll the JSON so `masked_text` with `[[REDACTED:SSN:fld_...]]` is visible.

### 3. Checking a value without receiving it (0:45–1:15)

Tool `verify_without_reveal`, placeholder = the `EMAIL` field.

First call, value `someone.else@example.com` → `matches: false`.

Then call it with `AVERY.MORGAN@EXAMPLE.COM` — deliberately upper-case.

> "Now say the agent already has an address from a CRM and needs to know if it's
> the same person. It gets a yes. Note I typed that in caps and it still matched,
> because the comparison is canonicalised — but the answer is still just a
> boolean. The field never comes back."

Point at `attempts_remaining` counting down.

> "And that's the important part. An unbounded equality check is a brute-force
> search, so it's rate limited per field and per session, compared as a keyed
> HMAC in constant time."

### 4. The reveal, and the fact that it's one-way (1:15–2:00)

This is the centrepiece. Don't rush it.

Tool `request_field_unmask`, placeholder = `EMAIL`, reason:

```text
Sending the settlement letter requires the claimant's email address
```

Call it. Point at the button sitting on `Waiting…`.

> "The tool call is now blocked server-side. There's no answer for it to return
> until a human decides."

The approval modal appears. Read the owner's view aloud:

> "The owner sees the category, a safe context hint, and the agent's stated
> reason — not the value they're about to release."

Approve. The value arrives.

> "One value, once. And look at the response — `consumed: true`, with a receipt
> id."

Now call `request_field_unmask` again on the same field, or re-run the retrieve.

> "Second attempt: `already_consumed`. Not denied, not an error — the grant is
> spent. That's deliberate. Once a value is in a model's context it may already
> be in a provider's logs or a downstream tool call, so revoking it is a fiction.
> The only property you can actually enforce is that the second caller gets
> nothing. We test that with fifty concurrent consumers; exactly one gets a value."

### 5. The agent pushing back, correctly (2:00–2:25)

This document has **two** fields the detector called `SSN`, and they are
indistinguishable in the dropdown. Click the placeholder on the `Claim
reference` line directly in the document — it highlights and becomes the
console's target. Do not use the dropdown here; picking the real SSN below it
would invert the whole point of the shot.

Tool `challenge_redaction`, target = the placeholder on the `Claim reference`
line, reasoning:

```text
This is the claim reference printed at the top of the form. It only looks like
an SSN because it shares the 3-2-4 digit shape.
```

> "Here's a real false positive — not one we staged. The detector flagged the
> claim reference as a Social Security number at 0.95 confidence. It's wrong, and
> the agent can see from context that it's wrong, but it has no legitimate way to
> say so. So we gave it one."

Approve. Point at the model assessment in the response, then at the document.

> "The field is now visible — in this session only. The detector's global output
> is untouched, and the view revision changed, which invalidates any cursor the
> agent was holding."

### 6. Injection, and why it does nothing (2:25–2:40)

Open `Helios Health — Incident Report 2026-0091`. Scroll to the notice block.

> "Last thing. Document text is attacker-controlled input, so this fixture has a
> prompt injection telling the agent to approve its own unmask requests. It can't.
> There's no tool that approves anything — approval is a different actor, in a
> different transaction. The tool that returns document text is annotated
> `untrustedContentHint` for exactly this reason."

Close on the audit table.

> "And every one of those events is in an append-only trail that commits in the
> same transaction as the action itself. Nothing here succeeds unlogged."

## If the modal races you

`REDACTLY_APPROVAL_TIMEOUT_SECONDS` defaults to 120, so you have two minutes to
click. If you want a longer safety margin while recording:

```bash
REDACTLY_APPROVAL_TIMEOUT_SECONDS=600 uv run uvicorn app.main:app --port 8000
```

`REDACTLY_GRANT_TTL_SECONDS` defaults to 60 — that's the window between approval
and the value being delivered, which is automatic, so it won't affect you unless
you pause mid-shot.

## What not to claim on camera

Say the limitation out loud rather than letting a judge find it. The approval
modal is on the same page an agent can drive, so a browser-resident agent could
watch the queue or race the click. Production would move approval out-of-band.
Naming that is stronger than hoping nobody notices.
