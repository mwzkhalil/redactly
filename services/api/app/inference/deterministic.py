"""Deterministic pattern detector.

Used by the invariant tests and by offline demos. Model output shifts between
revisions, and a security test that asserts "no raw value escapes" must fail for
security reasons only — never because the classifier changed its mind about a
span boundary. Set `REDACTLY_DETECTOR=deterministic` to select it.
"""

from __future__ import annotations

import re

from ..domain.enums import Category
from .base import DetectedSpan
from .span_resolution import resolve, trim_span

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)

# (compiled pattern, capture group, entity type, category, confidence)
_PATTERNS: tuple[tuple[re.Pattern[str], int, str, Category, float], ...] = (
    (
        re.compile(r"(?im)^(?:policyholder|policy holder|insured|claimant|patient|employee|full name|name)\s*:[ \t]*(.+)$"),
        1,
        "PERSON",
        Category.PERSON,
        0.99,
    ),
    (
        re.compile(r"(?im)^(?:address|mailing address|residence|home address)\s*:[ \t]*(.+)$"),
        1,
        "ADDRESS",
        Category.ADDRESS,
        0.99,
    ),
    (
        re.compile(r"(?im)^(?:claim reference|claim number|policy number|member id|case id|reference)\s*:[ \t]*(.+)$"),
        1,
        "ID_NUMBER",
        Category.ID_NUMBER,
        0.99,
    ),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), 0, "EMAIL_ADDRESS", Category.EMAIL, 0.98),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), 0, "US_SSN", Category.SSN, 0.97),
    (re.compile(r"\b\d{10,19}\b"), 0, "US_BANK_NUMBER", Category.ACCOUNT_NUMBER, 0.96),
    (
        re.compile(r"\+?\d{0,2}[ \t]*\(?\d{3}\)?[ \t.-]?\d{3}[ \t.-]?\d{4}\b"),
        0,
        "PHONE_NUMBER",
        Category.PHONE,
        0.95,
    ),
    (re.compile(rf"\b(?:{_MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b"), 0, "DATE_TIME", Category.DATE_OF_BIRTH, 0.94),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), 0, "DATE_TIME", Category.DATE_OF_BIRTH, 0.94),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"), 0, "DATE_TIME", Category.DATE_OF_BIRTH, 0.94),
    (re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?"), 0, "FINANCIAL", Category.MONETARY_AMOUNT, 0.93),
)


class DeterministicDetector:
    name = "redactly/deterministic-patterns"
    revision = "1"
    onnx_sha256 = None

    def detect(self, text: str) -> list[DetectedSpan]:
        spans: list[DetectedSpan] = []
        for pattern, group, entity_type, category, confidence in _PATTERNS:
            for match in pattern.finditer(text):
                trimmed = trim_span(text, match.start(group), match.end(group))
                if trimmed is None:
                    continue
                start, end = trimmed
                spans.append(
                    DetectedSpan(
                        start=start,
                        end=end,
                        base_entity_type=entity_type,
                        category=str(category),
                        confidence=confidence,
                    )
                )
        return resolve(text, spans)
