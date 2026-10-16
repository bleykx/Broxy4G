"""Garde anti-tempête du reset de dongle.

Un reset de dongle force une ré-énumération USB destructrice (~15-50 s de
panne) via ``mmcli --reset``. Un client externe (ex. Ankabot) qui tape sur
l'endpoint de reset en rafales ne doit pas pouvoir enchaîner des resets.

La garde amortit les resets par dongle :
- **cooldown** : un reset n'est exécuté qu'à l'issue d'un délai minimum
  depuis le dernier reset exécuté sur le même dongle ;
- **plafond de fenêtre** : au-delà d'un nombre maximal de resets par
  dongle et par fenêtre glissante, le reset est refusé.

Les refus ne touchent **jamais** le matériel : aucun verrou n'est pris et
aucune commande modem n'est lancée. L'état du dongle est conservé en mémoire
(un redémarrage du service réinitialise la garde).
"""

import os
import time
from collections import deque
from collections.abc import Callable

DEFAULT_COOLDOWN_SECONDS = 60.0
DEFAULT_MAX_PER_WINDOW = 5
DEFAULT_WINDOW_SECONDS = 600.0

COOLDOWN = "cooldown"
CAP = "cap"


def _env_float(name: str, default: float) -> float:
    """Lit une valeur float depuis l'environnement ; défaut si absente/invalid."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _env_int(name: str, default: int) -> int:
    """Lit une valeur int depuis l'environnement ; défaut si absente/invalid/<1."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


class ResetThrottle:
    """Limite cadence et nombre de resets exécutés par dongle (en mémoire)."""

    def __init__(
        self,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        max_per_window: int = DEFAULT_MAX_PER_WINDOW,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cooldown_seconds = cooldown_seconds
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        self._clock = clock
        self._history: dict[str, deque[float]] = {}

    def reject(self, key: str) -> str | None:
        """Retourne la raison du refus pour ``key`` (``cap`` / ``cooldown``) ou ``None`` si autorisé.

        Le plafond est évalué avant le cooldown : une rafale qui atteint le
        plafond de la fenêtre est refusée même si le cooldown a expiré.
        """
        now = self._clock()
        history = self._history.get(key)
        if history is None:
            return None
        # Purge : le plafond ne compte que les resets de la fenêtre courante.
        while history and now - history[0] >= self.window_seconds:
            history.popleft()
        if not history:
            return None
        if len(history) >= self.max_per_window:
            return CAP
        if now - history[-1] < self.cooldown_seconds:
            return COOLDOWN
        return None

    def record(self, key: str) -> None:
        """Enregistre un reset exécuté pour ``key`` (horloge de référence)."""
        self._history.setdefault(key, deque()).append(self._clock())

    @classmethod
    def from_env(cls) -> "ResetThrottle":
        """Construit la garde depuis l'environnement (défauts si absents/invalides)."""
        return cls(
            cooldown_seconds=_env_float("BROXY4G_RESET_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS),
            max_per_window=_env_int("BROXY4G_RESET_MAX_PER_WINDOW", DEFAULT_MAX_PER_WINDOW),
            window_seconds=_env_float("BROXY4G_RESET_WINDOW_SECONDS", DEFAULT_WINDOW_SECONDS),
        )
