"""Verdict de connectivité d'un dongle : sondes tri-état + hystérésis DEGRADED.

C'est le **seul** endroit qui décide d'entrer ou de sortir de ``DEGRADED``
pendant la vérification de connectivité de la reconciliation. Hystérésis
asymétrique (voir ``DegradedArbiter``) : la sortie de ``DEGRADED`` est
immédiate dès que l'IP publique est ``OK`` ; l'entrée attend
``min_consecutive`` échecs faibles consécutifs (blackhole concordant
IP+ping = immédiat).

Le compteur d'échecs faibles est **en mémoire, par dongle**, porté par
l'instance créée par le worker de reconciliation : un redémarrage du service
le réinitialise (comportement volontaire, comme le compteur de tentatives de
``UsbRecoveryService``).
"""

from uuid import UUID

from broxy4g.domain.degraded_arbiter import ArbiterDecision, DegradedArbiter, ProbeVerdict
from broxy4g.domain.dongle_states import DongleState
from broxy4g.services.dongle_diagnostics import DongleMetrics


class ConnectivityVerdictService:
    """Transforme les métriques d'uplink en décision d'état (ONLINE/DEGRADED)."""

    def __init__(self, arbiter: DegradedArbiter):
        self.arbiter = arbiter
        self._weak_failures: dict[UUID, int] = {}

    @staticmethod
    def ping_verdict(metrics: DongleMetrics) -> ProbeVerdict:
        """Verdict tri-état du ping : None (pas d'IP source) = ``NOT_RUN``."""
        ping = metrics.ping
        if ping is None:
            return ProbeVerdict.NOT_RUN
        return ProbeVerdict.OK if ping.packets_received > 0 else ProbeVerdict.FAILED

    def assess(self, dongle_id: UUID, metrics: DongleMetrics) -> DongleState | None:
        """Applique l'arbitre ; renvoie l'état cible ou ``None`` (KEEP_STATE)."""
        ip_verdict = metrics.public_ip_verdict or ProbeVerdict.NOT_RUN
        decision, weak = self.arbiter.decide(
            ip_verdict, self.ping_verdict(metrics), self._weak_failures.get(dongle_id, 0)
        )
        self._weak_failures[dongle_id] = weak
        if decision is ArbiterDecision.RESTORE_ONLINE:
            return DongleState.ONLINE
        if decision is ArbiterDecision.MARK_DEGRADED:
            return DongleState.DEGRADED
        return None
