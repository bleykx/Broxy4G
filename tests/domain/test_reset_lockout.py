"""Verrou manuel de reset (ResetLockout) : horloge contrôlable, pas d'I/O."""

from broxy4g.domain.reset_lockout import MAX_LOCK_SECONDS, ResetLockout


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_max_lock_seconds_is_24h():
    assert MAX_LOCK_SECONDS == 86400


def test_lock_sets_exact_deadline():
    clock = _Clock()
    lockout = ResetLockout(clock=clock)
    lockout.lock("a", 120.0)
    assert lockout.remaining("a") == 120.0
    assert lockout.is_locked("a")


def test_remaining_counts_down():
    clock = _Clock()
    lockout = ResetLockout(clock=clock)
    lockout.lock("a", 120.0)
    clock.t = 45.0
    assert lockout.remaining("a") == 75.0
    assert lockout.is_locked("a")


def test_expired_lock_returns_zero_and_is_purged():
    clock = _Clock()
    lockout = ResetLockout(clock=clock)
    lockout.lock("a", 10.0)
    clock.t = 10.0  # échéance exacte : plus rien de restant
    assert lockout.remaining("a") == 0.0
    assert not lockout.is_locked("a")
    assert "a" not in lockout._until  # l'entrée expirée est purgée


def test_remaining_zero_for_absent_key():
    lockout = ResetLockout()
    assert lockout.remaining("absent") == 0.0
    assert not lockout.is_locked("absent")


def test_lock_zero_or_negative_unlocks():
    clock = _Clock()
    lockout = ResetLockout(clock=clock)
    lockout.lock("a", 5.0)
    lockout.lock("a", 0)  # seconds <= 0 → levée
    assert not lockout.is_locked("a")
    lockout.lock("b", 5.0)
    lockout.lock("b", -3)
    assert not lockout.is_locked("b")


def test_keys_are_isolated():
    lockout = ResetLockout()
    lockout.lock("a", 60.0)
    assert lockout.is_locked("a")
    assert not lockout.is_locked("b")
    lockout.unlock("b")  # ne touche pas l'autre clé
    assert lockout.is_locked("a")
    lockout.unlock("a")
    assert not lockout.is_locked("a")


def test_unlock_idempotent():
    lockout = ResetLockout()
    lockout.unlock("a")  # jamais armé : pas d'erreur
    lockout.unlock("a")
    lockout.lock("a", 60.0)
    lockout.unlock("a")
    lockout.unlock("a")
    assert not lockout.is_locked("a")


def test_relock_replaces_deadline():
    clock = _Clock()
    lockout = ResetLockout(clock=clock)
    lockout.lock("a", 60.0)
    clock.t = 10.0
    lockout.lock("a", 60.0)  # nouvelle échéance depuis t=10
    assert lockout.remaining("a") == 60.0
    clock.t = 70.0
    assert lockout.remaining("a") == 0.0
