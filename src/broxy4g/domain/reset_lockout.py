"""Verrou manuel temporaire des resets de proxy, par dongle (en mémoire).

Composant distinct du ``ResetLock`` (reset EN COURS, consommé par l'interlock
de réconciliation et par ``usb_recovery``), de ``ResetPostDoneWindow`` (répit
15 s post-`DONE`) et de ``ResetThrottle`` (garde anti-tempête) : ici l'opérateur
bloque *manuellement* les resets d'un dongle pendant ``seconds`` secondes. Le
verrou est armé uniquement à la fin d'un reset réellement ``DONE`` et est levé
par expiration ou par l'endpoint ``/unlock``. Pendant le verrou, toute demande
de reset renvoie le statut métier ``LOCK``. Le proxy **continue de servir** :
aucune action sur le matériel, nftables ou les conteneurs.
"""

import time
from collections.abc import Callable

# Borne de sécurité : 24 h maximum (fixe, pas de variable d'environnement).
MAX_LOCK_SECONDS = 86400


class ResetLockout:
    """Verrou manuel de reset avec échéance absolue, par clé (``str(dongle_id)``)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}

    def lock(self, key: str, seconds: float) -> None:
        """Pose l'échéance ``clock() + seconds`` ; ``seconds <= 0`` lève le verrou."""
        if seconds <= 0:
            self.unlock(key)
            return
        self._until[key] = self._clock() + seconds

    def remaining(self, key: str) -> float:
        """Secondes restantes (0.0 si absent ou expiré, en purgeant l'entrée expirée)."""
        until = self._until.get(key)
        if until is None:
            return 0.0
        left = until - self._clock()
        if left <= 0:
            del self._until[key]
            return 0.0
        return left

    def is_locked(self, key: str) -> bool:
        """True si le verrou manuel est encore en cours pour ``key``."""
        return self.remaining(key) > 0

    def unlock(self, key: str) -> None:
        """Lève le verrou manuel (idempotent)."""
        self._until.pop(key, None)
