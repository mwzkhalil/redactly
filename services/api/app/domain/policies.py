"""Policy rules that decide what may leave the trusted process."""

from __future__ import annotations

import re
from typing import Any

from .enums import Category

# Only these keys may ever appear in audit metadata. An unknown key is dropped
# rather than stored, so a future code path cannot accidentally widen the log.
ALLOWED_METADATA_KEYS = frozenset(
    {
        "category",
        "matches",
        "page_index",
        "has_more",
        "field_count",
        "masked_field_count",
        "visible_field_count",
        "view_revision",
        "field_version",
        "grant_state",
        "request_kind",
        "confidence_band",
        "limit",
        "returned",
        "attempts_in_window",
        "retry_after_seconds",
        "override",
        "receipt_id",
    }
)

MAX_METADATA_STRING = 120
MAX_FREE_TEXT = 280

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")

# Categories the detector may emit that are not worth masking on their own. They
# still get a field row so `challenge_redaction` has something to point at.
NON_SENSITIVE_CATEGORIES = frozenset({Category.ORGANIZATION})


def is_sensitive(category: str) -> bool:
    return category not in NON_SENSITIVE_CATEGORIES


def sanitise_free_text(value: str | None, limit: int = MAX_FREE_TEXT) -> str:
    """Normalise agent- or human-supplied prose before it is persisted.

    Agent `reason` and `purpose` strings are untrusted input that end up in the
    owner approval UI, so control characters are stripped to stop an agent from
    smuggling terminal escapes or newline-based spoofing into the human's view.
    """
    if not value:
        return ""
    collapsed = _WHITESPACE.sub(" ", _CONTROL_CHARACTERS.sub("", value)).strip()
    return collapsed[:limit]


def filter_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    filtered: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in ALLOWED_METADATA_KEYS:
            continue
        if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
            filtered[key] = value
        elif isinstance(value, str):
            filtered[key] = sanitise_free_text(value, MAX_METADATA_STRING)
        # Any other type (dict, list, bytes) is dropped: nested structures are
        # exactly how raw values sneak into logs.
    return filtered


def confidence_band(confidence: float) -> str:
    """Bucket a model score so the audit log never carries a precise fingerprint."""
    if confidence >= 0.9:
        return "high"
    if confidence >= 0.7:
        return "medium"
    return "low"


def length_band(length: int) -> str:
    if length <= 8:
        return "short"
    if length <= 24:
        return "medium"
    return "long"


def safe_context_hint(value: str, category: str) -> str:
    """Describe a redacted value structurally, without any of its content.

    An agent needs enough shape to decide whether a field is worth verifying or
    challenging. Character classes and a coarse length band give it that without
    narrowing the value space in any useful way.
    """
    has_digit = any(character.isdigit() for character in value)
    has_alpha = any(character.isalpha() for character in value)
    if has_digit and has_alpha:
        shape = "letters and digits"
    elif has_digit:
        shape = "digits only"
    elif has_alpha:
        shape = "letters only"
    else:
        shape = "punctuation only"
    return f"{category.lower()} field, {shape}, {length_band(len(value))} length"
