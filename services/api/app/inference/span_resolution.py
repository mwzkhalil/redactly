"""Turn token-level predictions into non-overlapping character spans.

Offsets are what the rest of the system trusts: a field is stored as a pair of
character offsets into encrypted text, so a sloppy span means either a leaked
character at the edge of a mask or an over-broad redaction.
"""

from __future__ import annotations

from typing import Any, Sequence

from .base import DetectedSpan
from .label_mapping import split_bio, to_category

_MERGEABLE_GAP = " ,-"
_MAX_MERGE_GAP = 2


def trim_span(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Shrink a span to its non-whitespace core, or drop it entirely."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None
    if not any(character.isalnum() for character in text[start:end]):
        return None
    return start, end


def aggregate_window(
    text: str,
    offsets: Sequence[Sequence[int]],
    label_ids: Sequence[int],
    probabilities: Any,
    id2label: dict[int, str],
) -> list[DetectedSpan]:
    spans: list[DetectedSpan] = []
    active: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal active
        if active is None:
            return
        trimmed = trim_span(text, active["start"], active["end"])
        if trimmed is not None:
            start, end = trimmed
            entity_type = active["entity_type"]
            spans.append(
                DetectedSpan(
                    start=start,
                    end=end,
                    base_entity_type=entity_type,
                    category=str(to_category(entity_type)),
                    # The weakest token in a span bounds the confidence of the
                    # whole span; averaging would hide a single unsure token.
                    confidence=round(float(min(active["token_confidences"])), 6),
                )
            )
        active = None

    for offset, label_id, token_probabilities in zip(offsets, label_ids, probabilities):
        start, end = int(offset[0]), int(offset[1])
        if start == end:  # special token
            continue
        prefix, entity_type = split_bio(id2label[int(label_id)])
        if entity_type == "O":
            finish()
            continue

        confidence = float(token_probabilities[int(label_id)])
        continues = (
            active is not None
            and active["entity_type"] == entity_type
            and prefix == "I"
            and start <= active["end"] + 1
        )
        if continues:
            assert active is not None
            active["end"] = end
            active["token_confidences"].append(confidence)
        else:
            finish()
            active = {
                "start": start,
                "end": end,
                "entity_type": entity_type,
                "token_confidences": [confidence],
            }

    finish()
    return spans


def merge_adjacent(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Join same-category spans separated only by a comma, hyphen, or space.

    Multi-part values such as a street address routinely arrive as several spans.
    Masking them as one field keeps the placeholder count honest and stops the
    separators from spelling out the structure of the hidden value.
    """
    merged: list[DetectedSpan] = []
    for span in sorted(spans, key=lambda item: (item.start, item.end)):
        if merged:
            previous = merged[-1]
            gap = text[previous.end : span.start]
            if (
                previous.category == span.category
                and len(gap) <= _MAX_MERGE_GAP
                and all(character in _MERGEABLE_GAP for character in gap)
            ):
                merged[-1] = DetectedSpan(
                    start=previous.start,
                    end=span.end,
                    base_entity_type=previous.base_entity_type,
                    category=previous.category,
                    confidence=min(previous.confidence, span.confidence),
                )
                continue
        merged.append(span)
    return merged


def resolve(text: str, spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Deduplicate overlaps, preferring the longer and then more confident span."""
    ordered = sorted(spans, key=lambda item: (-(item.end - item.start), -item.confidence, item.start))
    kept: list[DetectedSpan] = []
    for span in ordered:
        if any(span.start < existing.end and existing.start < span.end for existing in kept):
            continue
        kept.append(span)
    return merge_adjacent(text, kept)
