"""Masked document rendering and cursor pagination.

The masked string is assembled from decrypted text inside this process and the
raw slices are dropped on return. Only placeholders and non-sensitive runs of
text are ever part of the value handed back to a route.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass, field as dataclass_field

from ..config import get_settings
from ..domain.enums import ToolStatus
from . import cursors, redaction
from .redaction import FieldView

PLACEHOLDER_TEMPLATE = "[[REDACTED:{category}:{field_ref}]]"


def render_placeholder(view: FieldView) -> str:
    return PLACEHOLDER_TEMPLATE.format(category=view.category, field_ref=view.field_ref)


@dataclass(slots=True)
class Page:
    text: str = ""
    fields: list[FieldView] = dataclass_field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Token:
    text: str
    view: FieldView | None


def build_tokens(text: str, fields: list[FieldView]) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    for view in sorted(fields, key=lambda item: item.start_offset):
        if view.start_offset < position:
            # Overlapping spans were already resolved at ingest; skip defensively
            # rather than emit a placeholder over text that is partly rendered.
            continue
        if view.start_offset > position:
            tokens.append(_Token(text[position : view.start_offset], None))
        raw = text[view.start_offset : view.end_offset]
        tokens.append(_Token(render_placeholder(view) if view.masked else raw, view))
        position = view.end_offset
    if position < len(text):
        tokens.append(_Token(text[position:], None))
    return tokens


def _break_point(chunk: str, room: int) -> int:
    """Prefer a line break, then a space, so pages end at a readable boundary."""
    window = chunk[:room]
    for separator in ("\n", " "):
        index = window.rfind(separator)
        if index > 0:
            return index + 1
    return max(1, room)


def paginate(tokens: list[_Token], page_characters: int) -> list[Page]:
    pages: list[Page] = []
    current = Page()
    length = 0
    queue = deque(tokens)

    def flush() -> None:
        nonlocal current, length
        if current.text or current.fields:
            pages.append(current)
        current = Page()
        length = 0

    while queue:
        token = queue.popleft()
        if token.view is not None:
            # A rendered field is atomic: splitting a placeholder across pages
            # would produce a reference an agent cannot resolve.
            if length and length + len(token.text) > page_characters:
                flush()
            current.text += token.text
            current.fields.append(token.view)
            length += len(token.text)
            continue

        remaining = token.text
        while remaining:
            room = page_characters - length
            if room <= 0:
                flush()
                room = page_characters
            if len(remaining) <= room:
                current.text += remaining
                length += len(remaining)
                break
            cut = _break_point(remaining, room)
            current.text += remaining[:cut]
            remaining = remaining[cut:]
            flush()

    flush()
    return pages or [Page()]


def describe_field(view: FieldView) -> dict[str, object]:
    """Field metadata for the masked view.

    Model confidence is deliberately absent. It is available only in a challenge
    response, where a human is already reviewing the classification.
    """
    return {
        "field_ref": view.field_ref,
        "category": view.category,
        "masked": view.masked,
        "safe_context_hint": view.safe_context_hint,
    }


def build_view(
    handle: sqlite3.Connection,
    *,
    session_document: sqlite3.Row,
    cursor: str | None,
) -> dict[str, object]:
    settings = get_settings()
    session_document_id = int(session_document["id"])
    view_revision = int(session_document["view_revision"])

    page_index = 0
    if cursor:
        payload = cursors.decode(cursor)
        if not payload or payload.get("d") != session_document_id:
            return {"status": str(ToolStatus.STALE), "reason": "invalid_cursor"}
        if payload.get("r") != view_revision:
            # A challenge changed what is masked. Silently continuing would let
            # an agent stitch together two inconsistent halves of a document.
            return {"status": str(ToolStatus.STALE), "reason": "view_revision_changed"}
        page_index = int(payload.get("p", 0))

    # Masking is derived entirely from the session's field rows, so an incomplete
    # mapping would render the missing spans as plain text rather than error. Fail
    # closed: a document that cannot be masked is not a document to hand over.
    if redaction.unmapped_field_count(handle, session_document_id=session_document_id):
        return {"status": str(ToolStatus.UNAVAILABLE), "reason": "view_not_materialised"}

    text = redaction.document_text(handle, session_document_id=session_document_id)
    fields = redaction.list_fields(handle, session_document_id=session_document_id)
    pages = paginate(build_tokens(text, fields), settings.view_page_characters)

    if page_index < 0 or page_index >= len(pages):
        return {"status": str(ToolStatus.STALE), "reason": "cursor_out_of_range"}

    page = pages[page_index]
    has_more = page_index + 1 < len(pages)
    next_cursor = (
        cursors.encode({"d": session_document_id, "r": view_revision, "p": page_index + 1})
        if has_more
        else None
    )
    return {
        "status": str(ToolStatus.OK),
        "title": session_document["title"],
        "view_revision": view_revision,
        "page_index": page_index,
        "page_count": len(pages),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "masked_text": page.text,
        "fields": [describe_field(view) for view in page.fields],
        "masked_field_count": sum(1 for view in page.fields if view.masked),
        "visible_field_count": sum(1 for view in page.fields if not view.masked),
    }
