import pytest

from broxy4g.domain.reset_states import (
    InvalidResetTransition,
    ResetInProgressError,
    ResetLock,
    ResetState,
    can_reset_transition,
    reset_transition,
)


def test_valid_reset_transitions():
    assert can_reset_transition(ResetState.IDLE, ResetState.RUNNING)
    assert reset_transition(ResetState.IDLE, ResetState.RUNNING) == ResetState.RUNNING

    assert can_reset_transition(ResetState.RUNNING, ResetState.DONE)
    assert reset_transition(ResetState.RUNNING, ResetState.DONE) == ResetState.DONE

    assert can_reset_transition(ResetState.RUNNING, ResetState.ERROR)
    assert reset_transition(ResetState.RUNNING, ResetState.ERROR) == ResetState.ERROR


def test_invalid_reset_transitions():
    assert not can_reset_transition(ResetState.IDLE, ResetState.DONE)
    with pytest.raises(InvalidResetTransition):
        reset_transition(ResetState.IDLE, ResetState.DONE)

    assert not can_reset_transition(ResetState.DONE, ResetState.RUNNING)
    with pytest.raises(InvalidResetTransition):
        reset_transition(ResetState.DONE, ResetState.RUNNING)

    assert not can_reset_transition(ResetState.ERROR, ResetState.RUNNING)
    with pytest.raises(InvalidResetTransition):
        reset_transition(ResetState.ERROR, ResetState.RUNNING)


def test_reset_lock_acquire_release():
    lock = ResetLock()
    token = lock.acquire("dongle-1")
    assert lock.is_locked("dongle-1")
    lock.release("dongle-1", token)
    assert not lock.is_locked("dongle-1")


def test_reset_lock_busy():
    lock = ResetLock()
    lock.acquire("dongle-1")
    with pytest.raises(ResetInProgressError):
        lock.acquire("dongle-1")


def test_reset_lock_wrong_token():
    lock = ResetLock()
    token = lock.acquire("dongle-1")
    with pytest.raises(ValueError):
        lock.release("dongle-1", "wrong-token")
    # Le verrou est toujours verrouillé
    assert lock.is_locked("dongle-1")
    lock.release("dongle-1", token)


def test_reset_lock_reacquire_after_release():
    lock = ResetLock()
    token1 = lock.acquire("dongle-1")
    lock.release("dongle-1", token1)
    token2 = lock.acquire("dongle-1")
    assert token2 != token1
    lock.release("dongle-1", token2)
