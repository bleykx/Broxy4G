"""Résultats métier du reset de dongle : statut, messages et refus de garde.

Porté hors de ``services/reset.py`` pour garder ce dernier sous la limite de
taille ; le comportement (messages exacts) n'y change pas.
"""

from dataclasses import dataclass

from broxy4g.services.reset_throttle import CAP, ResetThrottle


@dataclass
class ResetOutcome:
    status: str
    message: str | None = None


def throttled_outcome(reason: str, throttle: ResetThrottle) -> ResetOutcome:
    """Statut métier d'un reset refusé par la garde anti-tempête (pas de matériel touché)."""
    if reason == CAP:
        return ResetOutcome(
            "ERROR",
            f"reset refusé : trop de resets rapprochés (max {throttle.max_per_window} par {throttle.window_seconds:g}s)",
        )
    return ResetOutcome(
        "PENDING",
        f"reset en cooldown : un reset récent (< {throttle.cooldown_seconds:g}s) est déjà appliqué",
    )
