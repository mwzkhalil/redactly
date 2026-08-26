"""The state machines are closed.

No path leads back into PENDING or READY. Without this, "already consumed" would
be a transient condition rather than a permanent one.
"""

from __future__ import annotations

import pytest

from app.domain.enums import GrantState, RequestStatus
from app.domain.state_machines import (
    GRANT_TRANSITIONS,
    REQUEST_TRANSITIONS,
    can_transition_grant,
    can_transition_request,
    is_terminal_grant,
    is_terminal_request,
)


@pytest.mark.parametrize("status", [status for status in RequestStatus if status is not RequestStatus.PENDING])
def test_no_request_state_returns_to_pending(status):
    assert RequestStatus.PENDING not in REQUEST_TRANSITIONS[status]
    assert is_terminal_request(status)


@pytest.mark.parametrize("state", [state for state in GrantState if state is not GrantState.READY])
def test_no_grant_state_returns_to_ready(state):
    assert GrantState.READY not in GRANT_TRANSITIONS[state]
    assert is_terminal_grant(state)


def test_every_state_is_reachable_from_the_initial_state():
    assert set(REQUEST_TRANSITIONS[RequestStatus.PENDING]) == set(RequestStatus) - {RequestStatus.PENDING}
    assert set(GRANT_TRANSITIONS[GrantState.READY]) == set(GrantState) - {GrantState.READY}


def test_consumed_grant_cannot_be_reconsumed():
    assert not can_transition_grant(GrantState.CONSUMED, GrantState.CONSUMED)
    assert can_transition_grant(GrantState.READY, GrantState.CONSUMED)


def test_approved_request_cannot_be_reopened():
    assert not can_transition_request(RequestStatus.APPROVED, RequestStatus.PENDING)
    assert can_transition_request(RequestStatus.PENDING, RequestStatus.APPROVED)
