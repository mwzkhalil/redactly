"""Map detector labels onto Redactly categories.

Model label sets are unstable across revisions, so an unrecognised label falls
back to OTHER and is still masked. Failing open here would mean a renamed label
silently stops redacting a field.
"""

from __future__ import annotations

from ..domain.enums import Category

CANONICAL_TYPES: dict[str, Category] = {
    "PERSON": Category.PERSON,
    "PER": Category.PERSON,
    "NAME": Category.PERSON,
    "HONORIFIC": Category.PERSON,
    "TITLE": Category.PERSON,
    "EMAIL_ADDRESS": Category.EMAIL,
    "EMAIL": Category.EMAIL,
    "PHONE_NUMBER": Category.PHONE,
    "PHONE": Category.PHONE,
    "US_SSN": Category.SSN,
    "SSN": Category.SSN,
    "US_ITIN": Category.ID_NUMBER,
    "US_DRIVER_LICENSE": Category.ID_NUMBER,
    "US_PASSPORT": Category.ID_NUMBER,
    "PASSPORT": Category.ID_NUMBER,
    "ID": Category.ID_NUMBER,
    "US_BANK_NUMBER": Category.ACCOUNT_NUMBER,
    "IBAN_CODE": Category.ACCOUNT_NUMBER,
    "CREDIT_CARD": Category.ACCOUNT_NUMBER,
    "BANK_ACCOUNT": Category.ACCOUNT_NUMBER,
    "LOCATION": Category.ADDRESS,
    "LOC": Category.ADDRESS,
    "ADDRESS": Category.ADDRESS,
    "STREET_ADDRESS": Category.ADDRESS,
    "DATE_TIME": Category.DATE_OF_BIRTH,
    "DATE": Category.DATE_OF_BIRTH,
    "DATE_OF_BIRTH": Category.DATE_OF_BIRTH,
    "FINANCIAL": Category.MONETARY_AMOUNT,
    "MONEY": Category.MONETARY_AMOUNT,
    "ORGANIZATION": Category.ORGANIZATION,
    "ORG": Category.ORGANIZATION,
    "COMPANY": Category.ORGANIZATION,
}


def split_bio(label: str) -> tuple[str, str]:
    """Return `(prefix, entity_type)` for a BIO or bare label."""
    if label == "O":
        return "O", "O"
    for separator in ("-", "_"):
        if len(label) > 2 and label[0] in {"B", "I"} and label[1] == separator:
            return label[0], label[2:]
    return "B", label


def to_category(entity_type: str) -> Category:
    return CANONICAL_TYPES.get(entity_type.upper(), Category.OTHER)
