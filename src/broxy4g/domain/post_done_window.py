"""Fenêtre de répit après un reset réussi (DONE), par dongle (en mémoire).

Composant distinct du ``ResetLock`` (reset EN COURS, consommé par l'interlock
de réconciliation et par ``usb_recovery``) et de la garde anti-tempête
(``ResetThrottle``) : ici on refuse, avec ``PENDING``, une nouvelle demande de
reset pendant ``seconds`` après un DONE, pour laisser l'exploitant travailler
sans être déconnecté avant d'avoir obtenu sa nouvelle IP publique.
"""

import time
from collections.abc import Callable


class ResetPostDoneWindow:
    """Fenêtre de répit après un reset réussi (DONE), par dongle.

    Pendant ``seconds`` après un DONE, une nouvelle demande de reset doit être
    refusée avec PENDING pour laisser l'exploitant travailler sans être
    déconnecté. Distinct du ResetLock (reset EN COURS) et du throttle anti-tempête.
    """

    def __init__(self, seconds: float = 15.0, clock: Callable[[], float] = time.monotonic):
        self.seconds = seconds
        self._clock = clock
        self._done_at: dict[str, float] = {}

    def mark_done(self, key: str) -> None:
        self._done_at[key] = self._clock()

    def in_window(self, key: str) -> bool:
        at = self._done_at.get(key)
        if at is None:
            return False
        if self._clock() - at >= self.seconds:
            del self._done_at[key]
            return False
        return True
