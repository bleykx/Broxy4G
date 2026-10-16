"""Tests unitaires de la garde anti-tempête du reset (horloge factice contrôlable)."""

import pytest

from broxy4g.services.reset_throttle import (
    COOLDOWN,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_MAX_PER_WINDOW,
    DEFAULT_WINDOW_SECONDS,
    ResetThrottle,
)


class FakeClock:
    """Horloge factice : ``now`` manipulable pour simuler l'avancée du temps."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_throttle(clock: FakeClock, **kwargs) -> ResetThrottle:
    return ResetThrottle(clock=clock, **kwargs)


def test_first_reject_is_none():
    throttle = make_throttle(FakeClock())
    assert throttle.reject("d1") is None


def test_reject_cooldown_after_record():
    clock = FakeClock()
    throttle = make_throttle(clock)
    throttle.record("d1")
    assert throttle.reject("d1") == COOLDOWN


def test_reject_none_after_cooldown_elapsed():
    clock = FakeClock()
    throttle = make_throttle(clock)
    throttle.record("d1")
    clock.advance(DEFAULT_COOLDOWN_SECONDS + 1)
    assert throttle.reject("d1") is None


def test_reject_cap_after_max_per_window_records():
    clock = FakeClock()
    throttle = make_throttle(clock)
    for _ in range(DEFAULT_MAX_PER_WINDOW):
        assert throttle.reject("d1") is None
        throttle.record("d1")
        clock.advance(DEFAULT_COOLDOWN_SECONDS + 1)  # hors cooldown, dans la fenêtre
    assert throttle.reject("d1") == "cap"


def test_history_pruned_after_window_elapsed():
    clock = FakeClock()
    throttle = make_throttle(clock)
    for _ in range(DEFAULT_MAX_PER_WINDOW):
        throttle.record("d1")
        clock.advance(DEFAULT_COOLDOWN_SECONDS + 1)
    assert throttle.reject("d1") == "cap"
    clock.advance(DEFAULT_WINDOW_SECONDS + 1)
    assert throttle.reject("d1") is None


def test_keys_are_independent():
    clock = FakeClock()
    throttle = make_throttle(clock)
    throttle.record("d1")
    assert throttle.reject("d1") == COOLDOWN
    assert throttle.reject("d2") is None


def test_from_env_defaults(monkeypatch):
    monkeypatch.delenv("BROXY4G_RESET_COOLDOWN_SECONDS", raising=False)
    monkeypatch.delenv("BROXY4G_RESET_MAX_PER_WINDOW", raising=False)
    monkeypatch.delenv("BROXY4G_RESET_WINDOW_SECONDS", raising=False)
    throttle = ResetThrottle.from_env()
    assert throttle.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS
    assert throttle.max_per_window == DEFAULT_MAX_PER_WINDOW
    assert throttle.window_seconds == DEFAULT_WINDOW_SECONDS


def test_from_env_reads_valid_values(monkeypatch):
    monkeypatch.setenv("BROXY4G_RESET_COOLDOWN_SECONDS", "12.5")
    monkeypatch.setenv("BROXY4G_RESET_MAX_PER_WINDOW", "3")
    monkeypatch.setenv("BROXY4G_RESET_WINDOW_SECONDS", "120")
    throttle = ResetThrottle.from_env()
    assert throttle.cooldown_seconds == 12.5
    assert throttle.max_per_window == 3
    assert throttle.window_seconds == 120.0


@pytest.mark.parametrize(
    ("cooldown", "max_per", "window"),
    [
        ("abc", "xyz", "1e"),
        ("-1", "-5", "-0.5"),
    ],
)
def test_from_env_falls_back_on_invalid_or_negative(
    monkeypatch, cooldown, max_per, window
):
    monkeypatch.setenv("BROXY4G_RESET_COOLDOWN_SECONDS", cooldown)
    monkeypatch.setenv("BROXY4G_RESET_MAX_PER_WINDOW", max_per)
    monkeypatch.setenv("BROXY4G_RESET_WINDOW_SECONDS", window)
    throttle = ResetThrottle.from_env()
    assert throttle.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS
    assert throttle.max_per_window == DEFAULT_MAX_PER_WINDOW
    assert throttle.window_seconds == DEFAULT_WINDOW_SECONDS
