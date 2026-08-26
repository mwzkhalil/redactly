"""Shared vocabulary.

These strings cross the tool boundary, so they are treated as part of the public
contract: an agent may branch on them and a judge may read them in the audit log.
"""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    SSN = "SSN"
    ID_NUMBER = "ID_NUMBER"
    ACCOUNT_NUMBER = "ACCOUNT_NUMBER"
    ADDRESS = "ADDRESS"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    MONETARY_AMOUNT = "MONETARY_AMOUNT"
    ORGANIZATION = "ORGANIZATION"
    OTHER = "OTHER"


class ProcessingStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class Override(StrEnum):
    NONE = "NONE"
    NON_SENSITIVE = "NON_SENSITIVE"


class RequestKind(StrEnum):
    UNMASK = "UNMASK"
    CHALLENGE = "CHALLENGE"


class RequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class GrantState(StrEnum):
    READY = "READY"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class Actor(StrEnum):
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class EventType(StrEnum):
    DOCUMENT_VIEWED = "DOCUMENT_VIEWED"
    FIELD_VERIFIED = "FIELD_VERIFIED"
    VERIFICATION_RATE_LIMITED = "VERIFICATION_RATE_LIMITED"
    UNMASK_REQUESTED = "UNMASK_REQUESTED"
    UNMASK_RESOLVED = "UNMASK_RESOLVED"
    REVEAL_CONSUMED = "REVEAL_CONSUMED"
    REVEAL_REPLAY_REJECTED = "REVEAL_REPLAY_REJECTED"
    CHALLENGE_REQUESTED = "CHALLENGE_REQUESTED"
    CHALLENGE_RESOLVED = "CHALLENGE_RESOLVED"
    FIELD_RECLASSIFIED = "FIELD_RECLASSIFIED"
    AUDIT_LOG_READ = "AUDIT_LOG_READ"


class ToolStatus(StrEnum):
    """Discriminants returned to the agent. Never a backend error string."""

    OK = "ok"
    REVEALED = "revealed"
    CHECKED = "checked"
    APPROVED = "approved"
    REJECTED = "rejected"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    STALE = "stale"
    ALREADY_CONSUMED = "already_consumed"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    NOT_SENSITIVE = "not_sensitive"
    UNAVAILABLE = "unavailable"


TERMINAL_REQUEST_STATUSES = frozenset(
    {
        RequestStatus.APPROVED,
        RequestStatus.DENIED,
        RequestStatus.TIMED_OUT,
        RequestStatus.CANCELLED,
        RequestStatus.STALE,
    }
)

REQUEST_STATUS_TO_TOOL_STATUS = {
    RequestStatus.DENIED: ToolStatus.DENIED,
    RequestStatus.TIMED_OUT: ToolStatus.TIMED_OUT,
    RequestStatus.CANCELLED: ToolStatus.CANCELLED,
    RequestStatus.STALE: ToolStatus.STALE,
}
