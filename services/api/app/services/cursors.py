"""Opaque, tamper-evident pagination cursors.

Row-level scoping already happens in SQL, so a forged cursor could only move an
offset within the caller's own data. Signing them anyway keeps the cursor a pure
continuation token: an agent cannot craft one to probe whether an id exists.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from ..security.crypto import derive_key

_CURSOR_INFO = b"redactly/cursor/v1"
_SIGNATURE_BYTES = 16


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def encode(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(derive_key(_CURSOR_INFO), body, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    return f"{_b64encode(body)}.{_b64encode(signature)}"


def decode(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        encoded_body, encoded_signature = token.split(".", 1)
        body = _b64decode(encoded_body)
        signature = _b64decode(encoded_signature)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(derive_key(_CURSOR_INFO), body, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
