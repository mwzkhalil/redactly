"""Type-aware canonicalisation for `verify_without_reveal`.

An agent that has a value from another source should be able to confirm a match
without knowing our exact formatting. Canonicalisation makes `(202) 555-0147`
and `+1 202 555 0147` compare equal, while keeping the comparison a single
equality test rather than a similarity score that could be climbed.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from ..domain.enums import Category

CANONICALIZATION_VERSION = 1

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_NON_DIGIT = re.compile(r"\D+")
_WHITESPACE = re.compile(r"\s+")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d %Y",
)


def _fold(value: str) -> str:
    # NFKC first so full-width or composed lookalikes cannot dodge an equality
    # check that the human owner would consider a match.
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _digits(value: str) -> str:
    return _NON_DIGIT.sub("", value)


def _alphanumeric(value: str) -> str:
    return _NON_ALNUM.sub("", _fold(value))


def _words(value: str) -> str:
    return _WHITESPACE.sub(" ", _NON_ALNUM.sub(" ", _fold(value))).strip()


def _phone(value: str) -> str:
    digits = _digits(value)
    # Keep the subscriber number so country-code presence does not change equality.
    return digits[-10:] if len(digits) > 10 else digits


def _date(value: str) -> str:
    cleaned = _WHITESPACE.sub(" ", value.replace(",", ", ").strip())
    for pattern in _DATE_FORMATS:
        try:
            parsed: date = datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
        return parsed.isoformat()
    return _words(value)


def _money(value: str) -> str:
    digits = re.sub(r"[^0-9.]+", "", value)
    if not digits:
        return ""
    try:
        return f"{float(digits):.2f}"
    except ValueError:
        return digits


_CANONICALISERS = {
    Category.EMAIL: _fold,
    Category.PHONE: _phone,
    Category.SSN: _digits,
    Category.ID_NUMBER: _alphanumeric,
    Category.ACCOUNT_NUMBER: _alphanumeric,
    Category.PERSON: _words,
    Category.ADDRESS: _words,
    Category.ORGANIZATION: _words,
    Category.DATE_OF_BIRTH: _date,
    Category.MONETARY_AMOUNT: _money,
    Category.OTHER: _words,
}


def canonicalise(value: str, category: str) -> str:
    try:
        resolved = Category(category)
    except ValueError:
        resolved = Category.OTHER
    return _CANONICALISERS.get(resolved, _words)(value)
