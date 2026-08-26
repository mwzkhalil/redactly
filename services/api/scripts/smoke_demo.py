"""End-to-end walkthrough against a running policy service.

Plays both roles — the agent calling tools and the owner answering prompts — so
the whole lifecycle can be exercised without a browser. Useful as a smoke test
and as a narration script for a demo.

    uv run python scripts/smoke_demo.py
    uv run python scripts/smoke_demo.py --base-url http://localhost:3000/api/backend

The script reads the fixture file directly when it needs a value to compare
against. That is not cheating around the boundary: it stands in for the case
`verify_without_reveal` exists to serve, where an agent already holds a value
from another system and only needs to know whether it matches.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
BASE_URL = DEFAULT_BASE_URL
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic"

# Fixtures contain em dashes; a cp1252 console would otherwise mangle them.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
LABELLED_PLACEHOLDER = re.compile(
    r"(?im)^([A-Za-z][A-Za-z ]{2,30}?)\s*:[ \t]*\[\[REDACTED:[A-Z_]+:(fld_[A-Za-z0-9_-]+)\]\]"
)

# Labels whose values are genuinely not personal identifiers. Choosing a target
# by category alone would pick a real date of birth and argue it is not one,
# which demonstrates nothing except a badly reasoned agent.
FALSE_POSITIVE_LABELS: dict[str, str] = {
    "claim reference": (
        "this is the claim reference printed at the top of the form; it only looks like an SSN "
        "because it shares the 3-2-4 digit shape"
    ),
    "claim number": "this is an internal claim number, not an identity document",
    "reference": "this is an internal file reference, not an identity document",
    "start date": "this is an employment start date, not the employee's date of birth",
    "starting salary": "this is a compensation figure, not a personal identifier",
    "claimed amount": "this is a settlement figure, not a personal identifier",
    "team": "this is the employer's own trading name, which is already public",
}


def show(step: str, payload: Any) -> None:
    print(f"\n=== {step} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:1400])


def known_email(slug: str) -> str | None:
    """A value the agent is standing in for having from another system."""
    path = FIXTURES / f"{slug}.txt"
    if not path.is_file():
        return None
    match = EMAIL_PATTERN.search(path.read_text(encoding="utf-8"))
    return match.group(0) if match else None


def pick_challenge_target(masked_text: str, exclude: str) -> tuple[str, str] | None:
    for match in LABELLED_PLACEHOLDER.finditer(masked_text):
        label = " ".join(match.group(1).split()).lower()
        field_ref = match.group(2)
        if field_ref != exclude and label in FALSE_POSITIVE_LABELS:
            return field_ref, FALSE_POSITIVE_LABELS[label]
    return None


def answer_next_request(client: httpx.Client, *, approve: bool, note: str) -> None:
    """Owner side: wait for a prompt to appear, then decide it."""
    for _ in range(200):
        pending = client.get(f"{BASE_URL}/owner/pending").json()["requests"]
        if pending:
            request = pending[0]
            print(
                f"    [owner] {request['kind']} on {request['category']} "
                f"({request['safe_context_hint']}) — reason: {request['reason']!r}"
            )
            client.post(
                f"{BASE_URL}/owner/requests/{request['request_ref']}/decision",
                json={"approve": approve, "note": note},
            )
            return
        time.sleep(0.1)
    raise SystemExit("no approval prompt appeared")


def call_with_owner(
    client: httpx.Client,
    *,
    path: str,
    body: dict[str, Any],
    approve: bool,
    note: str,
) -> dict[str, Any]:
    """Issue a blocking tool call while answering its prompt on another thread."""
    result: dict[str, Any] = {}

    def agent() -> None:
        result.update(client.post(f"{BASE_URL}{path}", json=body, timeout=180).json())

    thread = threading.Thread(target=agent)
    thread.start()
    answer_next_request(client, approve=approve, note=note)
    thread.join()
    return result


def main() -> int:
    global BASE_URL
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", default="claim-northstar-4471")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Use http://localhost:3000/api/backend to exercise the Next.js proxy path instead.",
    )
    arguments = parser.parse_args()
    BASE_URL = arguments.base_url.rstrip("/")
    slug = arguments.slug
    agent_base = f"/agent/documents/{slug}"

    with httpx.Client(timeout=60) as client:
        client.post(f"{BASE_URL}/session")
        show("documents", client.get(f"{BASE_URL}/documents").json())

        view = client.post(
            f"{BASE_URL}{agent_base}/view", json={"purpose": "reviewing this record for completeness"}
        ).json()
        show("request_document_view", view)

        masked = [field for field in view["fields"] if field["masked"]]
        if not masked:
            print("no masked fields on page 0", file=sys.stderr)
            return 1
        email = next((field for field in masked if field["category"] == "EMAIL"), masked[0])

        show(
            "verify_without_reveal (value we do not have)",
            client.post(
                f"{BASE_URL}{agent_base}/verify",
                json={
                    "field_ref": email["field_ref"],
                    "comparison_value": "someone.else@example.com",
                    "purpose": "checking a contact address from our CRM",
                },
            ).json(),
        )

        held = known_email(slug)
        if held:
            # Upper-cased on purpose: canonicalisation makes the match
            # format-tolerant without ever returning the stored form.
            show(
                "verify_without_reveal (value we do hold, still not returned)",
                client.post(
                    f"{BASE_URL}{agent_base}/verify",
                    json={
                        "field_ref": email["field_ref"],
                        "comparison_value": held.upper(),
                        "purpose": "confirming the CRM record matches this document",
                    },
                ).json(),
            )

        denied = call_with_owner(
            client,
            path=f"{agent_base}/unmask",
            body={"field_ref": email["field_ref"], "reason": "I would like to read this field"},
            approve=False,
            note="not justified by the task",
        )
        show("request_field_unmask (denied)", denied)

        revealed = call_with_owner(
            client,
            path=f"{agent_base}/unmask",
            body={
                "field_ref": email["field_ref"],
                "reason": "sending correspondence requires the contact address on file",
            },
            approve=True,
            note="approved for correspondence",
        )
        show("request_field_unmask (approved — the one and only delivery)", revealed)

        if revealed.get("request_ref"):
            show(
                "replaying the same approval",
                client.post(
                    f"{BASE_URL}{agent_base}/unmask/{revealed['request_ref']}/retrieve"
                ).json(),
            )

        target = pick_challenge_target(view["masked_text"], exclude=email["field_ref"])
        if target is None:
            print("\nno labelled false positive on page 0; skipping the challenge step")
        else:
            field_ref, reasoning = target
            show(
                "challenge_redaction (approved)",
                call_with_owner(
                    client,
                    path=f"{agent_base}/challenge",
                    body={"field_ref": field_ref, "reasoning": reasoning},
                    approve=True,
                    note="agreed, false positive",
                ),
            )
            show(
                "request_document_view after reclassification",
                client.post(
                    f"{BASE_URL}{agent_base}/view",
                    json={"purpose": "re-reading after the challenge was accepted"},
                ).json(),
            )

        show(
            "get_redaction_audit_log",
            client.post(
                f"{BASE_URL}{agent_base}/audit",
                json={"purpose": "reviewing what happened in this session", "limit": 50},
            ).json(),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
