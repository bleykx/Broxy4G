import pytest

from broxy4g.domain.dongle_states import DongleState, InvalidStateTransition, can_transition, transition


def test_valid_transitions():
    valid_transitions = [
        (DongleState.DISCOVERED, DongleState.CONFIGURING),
        (DongleState.DISCOVERED, DongleState.UNENROLLED),
        (DongleState.DISCOVERED, DongleState.OFFLINE),
        (DongleState.UNENROLLED, DongleState.CONFIGURING),
        (DongleState.UNENROLLED, DongleState.OFFLINE),
        (DongleState.CONFIGURING, DongleState.CONNECTING),
        (DongleState.CONFIGURING, DongleState.ERROR),
        (DongleState.CONFIGURING, DongleState.OFFLINE),
        (DongleState.CONNECTING, DongleState.ONLINE),
        (DongleState.CONNECTING, DongleState.ERROR),
        (DongleState.CONNECTING, DongleState.OFFLINE),
        (DongleState.ONLINE, DongleState.DEGRADED),
        (DongleState.ONLINE, DongleState.RESETTING),
        (DongleState.ONLINE, DongleState.OFFLINE),
        (DongleState.ONLINE, DongleState.ERROR),
        (DongleState.DEGRADED, DongleState.ONLINE),
        (DongleState.DEGRADED, DongleState.RESETTING),
        (DongleState.DEGRADED, DongleState.OFFLINE),
        (DongleState.DEGRADED, DongleState.ERROR),
        (DongleState.RESETTING, DongleState.ONLINE),
        (DongleState.RESETTING, DongleState.ERROR),
        (DongleState.RESETTING, DongleState.OFFLINE),
        (DongleState.RESETTING, DongleState.CONNECTING),
        (DongleState.OFFLINE, DongleState.CONNECTING),
        (DongleState.OFFLINE, DongleState.OFFLINE),
        (DongleState.OFFLINE, DongleState.RESETTING),
        (DongleState.ERROR, DongleState.CONNECTING),
        (DongleState.ERROR, DongleState.OFFLINE),
        (DongleState.ERROR, DongleState.RESETTING),
    ]
    for from_state, to_state in valid_transitions:
        assert can_transition(from_state, to_state)
        assert transition(from_state, to_state) == to_state


def test_invalid_transitions():
    invalid_transitions = [
        (DongleState.DISCOVERED, DongleState.ONLINE),
        (DongleState.CONFIGURING, DongleState.ONLINE),
        (DongleState.UNENROLLED, DongleState.ONLINE),
    ]
    for from_state, to_state in invalid_transitions:
        assert not can_transition(from_state, to_state)
        with pytest.raises(InvalidStateTransition):
            transition(from_state, to_state)


def test_can_transition():
    assert can_transition(DongleState.DISCOVERED, DongleState.CONFIGURING)
    assert not can_transition(DongleState.DISCOVERED, DongleState.ONLINE)


def test_transition_returns_new_state():
    new_state = transition(DongleState.DISCOVERED, DongleState.CONFIGURING)
    assert new_state == DongleState.CONFIGURING


def test_specific_cases():
    assert can_transition(DongleState.ONLINE, DongleState.RESETTING)
    assert can_transition(DongleState.RESETTING, DongleState.ONLINE)
    assert not can_transition(DongleState.DISCOVERED, DongleState.ONLINE)
    assert can_transition(DongleState.OFFLINE, DongleState.CONNECTING)
    assert can_transition(DongleState.ERROR, DongleState.CONNECTING)


def test_degraded_can_connecting():
    """DEGRADED → CONNECTING must be allowed for reconciliation reconnect."""
    assert can_transition(DongleState.DEGRADED, DongleState.CONNECTING)
    assert transition(DongleState.DEGRADED, DongleState.CONNECTING) == DongleState.CONNECTING
