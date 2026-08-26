"""Legal transitions for approval requests and reveal grants.

The tables are pure data so a test can assert the machine is closed (no path
from a terminal state back to PENDING or READY) without a database.

    PENDING ─ human deny ────────> DENIED
            ─ deadline reached ──> TIMED_OUT
            ─ tool/browser abort > CANCELLED
            ─ field/session moved> STALE
            ─ human approve ─────> APPROVED (+ grant READY, unmask only)

    READY   ─ deadline reached ──> EXPIRED
            ─ field version bump > REVOKED
            ─ first retrieval ───> CONSUMED (exactly one raw response)
"""

from __future__ import annotations

from .enums import GrantState, RequestStatus

REQUEST_TRANSITIONS: dict[RequestStatus, frozenset[RequestStatus]] = {
    RequestStatus.PENDING: frozenset(
        {
            RequestStatus.APPROVED,
            RequestStatus.DENIED,
            RequestStatus.TIMED_OUT,
            RequestStatus.CANCELLED,
            RequestStatus.STALE,
        }
    ),
    RequestStatus.APPROVED: frozenset(),
    RequestStatus.DENIED: frozenset(),
    RequestStatus.TIMED_OUT: frozenset(),
    RequestStatus.CANCELLED: frozenset(),
    RequestStatus.STALE: frozenset(),
}

GRANT_TRANSITIONS: dict[GrantState, frozenset[GrantState]] = {
    GrantState.READY: frozenset({GrantState.CONSUMED, GrantState.EXPIRED, GrantState.REVOKED}),
    GrantState.CONSUMED: frozenset(),
    GrantState.EXPIRED: frozenset(),
    GrantState.REVOKED: frozenset(),
}


def can_transition_request(current: str, target: str) -> bool:
    return RequestStatus(target) in REQUEST_TRANSITIONS[RequestStatus(current)]


def can_transition_grant(current: str, target: str) -> bool:
    return GrantState(target) in GRANT_TRANSITIONS[GrantState(current)]


def is_terminal_request(status: str) -> bool:
    return not REQUEST_TRANSITIONS[RequestStatus(status)]


def is_terminal_grant(state: str) -> bool:
    return not GRANT_TRANSITIONS[GrantState(state)]
