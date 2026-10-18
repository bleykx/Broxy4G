"""Unit tests de la fenêtre de répit post-DONE (``ResetPostDoneWindow``).

Composant mémoire par dongle : pendant ``seconds`` après un DONE, une nouvelle
demande de reset doit être refusée (PENDING) sans re-resetter le matériel.
"""

from broxy4g.domain.post_done_window import ResetPostDoneWindow


class Clock:
    """Horloge contrôlable."""

    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_no_window_before_any_done():
    window = ResetPostDoneWindow(seconds=15.0, clock=Clock())
    assert window.in_window("dongle-1") is False


def test_in_window_after_mark_done_and_per_dongle():
    window = ResetPostDoneWindow(seconds=15.0, clock=Clock())
    window.mark_done("dongle-1")
    assert window.in_window("dongle-1") is True
    assert window.in_window("dongle-2") is False  # isolée par dongle


def test_window_expires_after_seconds():
    clock = Clock()
    window = ResetPostDoneWindow(seconds=15.0, clock=clock)
    window.mark_done("dongle-1")

    clock.t = 14.9
    assert window.in_window("dongle-1") is True

    clock.t = 15.0
    assert window.in_window("dongle-1") is False


def test_refusals_never_remark_window():
    """Un refus (``in_window``) ne ré-horodate jamais : l'expiration reste
    ancrée au DONE, quel que soit le nombre de refus consécutifs."""
    clock = Clock()
    window = ResetPostDoneWindow(seconds=15.0, clock=clock)
    window.mark_done("dongle-1")

    clock.t = 5.0
    assert window.in_window("dongle-1") is True
    clock.t = 10.0
    assert window.in_window("dongle-1") is True  # refus consécutif

    clock.t = 15.0
    assert window.in_window("dongle-1") is False  # expire à 15 s, pas à 10 + 15
