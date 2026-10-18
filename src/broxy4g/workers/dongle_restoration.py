"""Restauration de la connexion et de la connectivité d'un dongle.

Pipeline par dongle extrait de ``workers/reconciliation.py`` : reconnexion si
le dongle n'est pas déjà actif, (re)pose des routes d'uplink sans toucher au
modem, puis vérification de la connectivité avec l'arbitre tri-état. Un échec
d'une étape ne doit jamais faire échouer tout le cycle.
"""

import logging

from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState, can_transition
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.connection import ConnectionService
from broxy4g.services.connectivity_verdict import ConnectivityVerdictService
from broxy4g.services.metrics_snapshot import MetricsSnapshot
from broxy4g.services.uplink_routes import UplinkRouteService

logger = logging.getLogger(__name__)


class DongleRestoration:
    """Restaure la connexion mobile, les routes et l'état d'un dongle."""

    def __init__(
        self,
        connection_service: ConnectionService,
        uplink_routes: UplinkRouteService,
        dongle_repo: DongleRepository,
        verdicts: ConnectivityVerdictService,
        metrics_snapshot: MetricsSnapshot,
    ):
        self.connection_service = connection_service
        self.uplink_routes = uplink_routes
        self.dongle_repo = dongle_repo
        self.verdicts = verdicts
        self.metrics_snapshot = metrics_snapshot

    async def restore_connection_if_needed(self, dongle: Dongle) -> Dongle:
        """Restaure la connexion si le dongle n'est pas déjà actif.

        Un dongle déjà actif (ONLINE/CONNECTING) n'est jamais reconnecté. Un
        DEGRADED dont le bearer est encore vivant (IP du bearer présente sur
        l'interface) non plus : connect() ferait flush_ipv4 + assign_address
        et changerait l'IP de bearer à chaque cycle ; seules les routes sont
        réparées ensuite, sans toucher au modem. Bearer mort/absent/IP périmée
        → connect() comme avant.

        Retourne le dongle, rechargé depuis le dépôt si connect() a tourné
        (connect() charge et persiste sa propre copie).
        """
        if dongle.state in (DongleState.ONLINE, DongleState.CONNECTING):
            return dongle
        if dongle.state == DongleState.DEGRADED and await self.connection_service.bearer_is_live(dongle):
            logger.info(
                "Reconciliation dongle %s (DEGRADED, bearer vivant) : sans reconnexion",
                dongle.id,
            )
            return dongle
        logger.info("Reconciliation dongle %s (état=%s)", dongle.id, dongle.state.value)
        await self._restore_connection(dongle)
        # connection_service.connect() charge et persiste sa propre copie ;
        # recharger pour que la vérification voie le nouvel état.
        refreshed = await self.dongle_repo.get_by_id(dongle.id)
        return refreshed if refreshed is not None else dongle

    async def _restore_connection(self, dongle) -> None:
        """Rétablit la connexion mobile et les routes."""
        try:
            await self.connection_service.connect(dongle.id)
        except Exception as exc:
            logger.exception("Échec restauration connexion dongle %s", dongle.id)
            dongle.last_error = f"{type(exc).__name__}: {exc}"
            if can_transition(dongle.state, DongleState.ERROR):
                dongle.change_state(DongleState.ERROR)
            await self.dongle_repo.save(dongle)

    async def repair_uplink_routes(self, dongle) -> None:
        """(Re)pose les routes d'uplink du dongle sans reconnexion du modem.

        Un échec de cette étape ne doit pas faire échouer tout le cycle.
        """
        try:
            await self.uplink_routes.apply_for_dongle(dongle)
        except Exception:
            logger.exception("Échec réparation routes uplink dongle %s", dongle.id)

    async def verify_connectivity(self, dongle) -> None:
        """Vérifie la connectivité et met à jour l'état (hystérésis DEGRADED).

        Le verdict binaire « pas d'IP publique → DEGRADED » est remplacé par
        l'arbitre tri-état (``services/connectivity_verdict.py``) : une sonde
        faible unique ne marque plus DEGRADED, la sortie est immédiate dès
        que l'IP publique revient.
        """
        try:
            metrics = await self.connection_service.get_metrics(dongle.id)
            signal = metrics.signal_quality.percent if metrics.signal_quality else None
            self.metrics_snapshot.update(dongle.id, metrics.public_ip, signal)
            target = self.verdicts.assess(dongle.id, metrics)
            if target is not None and dongle.state != target and can_transition(dongle.state, target):
                dongle.change_state(target)
                await self.dongle_repo.save(dongle)
        except Exception:
            logger.exception("Vérification connectivité échouée pour %s", dongle.id)
