"""Arbitrage d'entrée/sortie de ``DEGRADED`` avec hystérésis asymétrique.

La sonde d'IP publique échoue de deux façons indiscernables pour un client
 (« pas d'IP source sur l'interface » vs « curl a échoué ») ; sur un lien 3G
 lent, un verdict binaire « pas d'IP publique → DEGRADED » marquait le dongle
 dégradé à chaque à-coup et déclenchait des resets en boucle. Cet arbitre
 introduit une hystérésis :

- la **sortie** de ``DEGRADED`` est immédiate dès que l'IP publique est ``OK`` ;
- l'**entrée** est immédiate seulement si le blackhole est concordant
  (IP ``FAILED`` et ping != ``OK``), sinon elle attend ``min_consecutive``
  échecs faibles consécutifs.

Logique pure, sans I/O : le verdict de chaque sonde et le compteur d'échecs
faibles sont portés par l'appelant.
"""

from enum import Enum


class ProbeVerdict(Enum):
    """Verdict tri-état d'une sonde d'uplink."""

    OK = "OK"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"  # la sonde n'a pas pu être exécutée (pas d'IP source)


class ArbiterDecision(Enum):
    """Décision de l'arbitre."""

    KEEP_STATE = "KEEP_STATE"
    MARK_DEGRADED = "MARK_DEGRADED"
    RESTORE_ONLINE = "RESTORE_ONLINE"


class DegradedArbiter:
    """Décide l'entrée/sortie de DEGRADED à partir des verdicts de sondes."""

    def __init__(self, min_consecutive: int = 2):
        if min_consecutive < 1:
            raise ValueError("min_consecutive doit être >= 1")
        self.min_consecutive = min_consecutive

    def decide(
        self,
        ip_verdict: ProbeVerdict,
        ping_verdict: ProbeVerdict,
        weak_count: int,
    ) -> tuple[ArbiterDecision, int]:
        """Retourne ``(décision, nouveau_compteur_d'échecs_faibles)``.

        Règles :
        1. IP ``OK`` → ``RESTORE_ONLINE``, compteur remis à 0 (sortie immédiate).
        2. IP ``FAILED`` et ping != ``OK`` → ``MARK_DEGRADED`` immédiat
           (blackhole concordant, pas d'hystérésis).
        3. Sinon (``NOT_RUN``, ou ``FAILED`` avec ping ``OK``) → échec faible :
           compteur += 1, ``MARK_DEGRADED`` seulement si compteur >=
           ``min_consecutive``, sinon ``KEEP_STATE``.
        """
        if ip_verdict is ProbeVerdict.OK:
            return ArbiterDecision.RESTORE_ONLINE, 0
        if ip_verdict is ProbeVerdict.FAILED and ping_verdict is not ProbeVerdict.OK:
            return ArbiterDecision.MARK_DEGRADED, 0
        weak = weak_count + 1
        if weak >= self.min_consecutive:
            return ArbiterDecision.MARK_DEGRADED, weak
        return ArbiterDecision.KEEP_STATE, weak
