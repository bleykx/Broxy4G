import logging
from uuid import UUID

from broxy4g.adapters.adb import AdbAdapter
from broxy4g.adapters.connectivity import ConnectivityAdapter
from broxy4g.adapters.docker_compose import DockerComposeAdapter
from broxy4g.adapters.modem_manager import ModemManagerAdapter
from broxy4g.adapters.nftables import NftablesAdapter
from broxy4g.adapters.routing import RoutingAdapter
from broxy4g.domain.degraded_arbiter import DegradedArbiter
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.reset_states import ResetLock
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.connection import ConnectionService
from broxy4g.services.connectivity_verdict import ConnectivityVerdictService
from broxy4g.services.local_connections import LocalConnectionsService
from broxy4g.services.metrics_snapshot import MetricsSnapshot
from broxy4g.services.network_isolation import NetworkIsolationService
from broxy4g.services.proxy_network import ProxyNetworkService
from broxy4g.services.proxy_provisioning import ProxyProvisioningService
from broxy4g.services.routing_slot_migration import RoutingSlotMigration
from broxy4g.services.smartphone_uplink import SmartphoneUplinkService
from broxy4g.services.uplink_routes import UplinkRouteService
from broxy4g.services.usb_recovery import UsbRecoveryService
from broxy4g.workers.adb_soft_recovery import AdbSoftRecovery
from broxy4g.workers.dongle_restoration import DongleRestoration
from broxy4g.workers.proxy_reconciliation import ProxyReconciler

logger = logging.getLogger(__name__)


class ReconciliationWorker:
    """Restaure l'état désiré après reboot, crash ou reconnexion USB."""

    def __init__(
        self,
        dongle_repo: DongleRepository,
        proxy_repo: ProxyRepository,
        modem: ModemManagerAdapter,
        routing: RoutingAdapter,
        nft: NftablesAdapter,
        connectivity: ConnectivityAdapter,
        connection_service: ConnectionService,
        provisioning: ProxyProvisioningService,
        docker_compose: DockerComposeAdapter,
        metrics_snapshot: MetricsSnapshot,
        uplink_routes: UplinkRouteService,
        slot_migration: RoutingSlotMigration,
        isolation: NetworkIsolationService | None = None,
        reset_lock: ResetLock | None = None,
        local_connections: LocalConnectionsService | None = None,
        usb_recovery: UsbRecoveryService | None = None,
        proxy_network: ProxyNetworkService | None = None,
        min_consecutive: int = 2,
        adb: AdbAdapter | None = None,
        smartphone: SmartphoneUplinkService | None = None,
    ):
        """``min_consecutive`` = nb d'échecs faibles consécutifs avant DEGRADED.

        Le compteur d'échecs faibles associé (par dongle) est tenu **en
        mémoire** par le service de verdict : un redémarrage du service le
        réinitialise (comme le compteur de ``UsbRecoveryService``).
        """
        self.dongle_repo = dongle_repo
        self.connection_service = connection_service
        self.docker_compose = docker_compose
        self.metrics_snapshot = metrics_snapshot
        self.slot_migration = slot_migration
        self.isolation = isolation
        self.proxy_network = proxy_network
        self.usb_recovery = usb_recovery
        self.reset_lock = reset_lock
        self.proxy_reconciler = ProxyReconciler(
            proxy_repo=proxy_repo,
            dongle_repo=dongle_repo,
            provisioning=provisioning,
            docker_compose=docker_compose,
            local_connections=local_connections,
        )
        verdicts = ConnectivityVerdictService(DegradedArbiter(min_consecutive=min_consecutive))
        self.adb_soft_recovery = AdbSoftRecovery(
            adb=adb,
            smartphone=smartphone,
            dongle_repo=dongle_repo,
            usb_recovery=usb_recovery,
        )
        self.restoration = DongleRestoration(
            connection_service=connection_service,
            uplink_routes=uplink_routes,
            dongle_repo=dongle_repo,
            verdicts=verdicts,
            metrics_snapshot=metrics_snapshot,
        )

    async def reconcile_all(self) -> None:
        """Reconcilie tous les dongles puis tous les proxies enregistrés."""
        if self.usb_recovery is not None:
            self.usb_recovery.begin_cycle()
        await self._repair_slot_duplicates()
        dongles = await self.dongle_repo.list_all()
        for dongle in dongles:
            try:
                await self.reconcile_dongle(dongle.id)
            except Exception:
                logger.exception("Reconciliation échouée pour dongle %s", dongle.id)
        # Un conteneur proxy ne doit jamais démarrer avant que le réseau Docker
        # et l'isolation (droits de sortie + plancher) soient en place.
        await self._ensure_proxy_network()
        await self._reapply_isolation()
        await self.reconcile_proxies()
        # Réalignement final idempotent, après le démarrage des conteneurs.
        await self._reapply_isolation()

    async def _repair_slot_duplicates(self) -> None:
        """Répare les doublons de slots persistés avant toute restauration.

        Un échec de la migration ne doit pas empêcher le reste du cycle.
        """
        try:
            await self.slot_migration.repair_duplicates()
        except Exception:
            logger.exception("Échec réparation doublons de slots de routage")

    async def _ensure_proxy_network(self) -> None:
        """Vérifie que le réseau proxy est exploitable (réseau + passerelle).

        ``ensure_ready`` recrée le pont s'il est absent (reboot, suppression
        accidentelle) avant tout démarrage de conteneur proxy. Un échec de
        cette étape ne doit pas faire échouer tout le cycle.
        """
        if self.proxy_network is None:
            return
        try:
            await self.proxy_network.ensure_ready()
        except Exception:
            logger.exception("Échec vérification réseau proxy")

    async def _reapply_isolation(self) -> None:
        """Réaligne la table nftables d'isolation sur l'état persisté."""
        if self.isolation is None:
            return
        try:
            await self.isolation.apply()
        except Exception:
            logger.exception("Échec réapplication isolation réseau")

    async def reconcile_proxies(self) -> None:
        """Réaligne chaque proxy sur son état désiré (activé/désactivé)."""
        await self.proxy_reconciler.reconcile_proxies()

    def _reset_in_progress(self, dongle: Dongle, dongle_id: UUID) -> bool:
        """Un reset est-il réellement en cours pour ce dongle ?

        Un reset réellement en cours (RESETTING ET verrou tenu) est totalement
        ignoré : connexion, proxy ET connectivité. Le reset gère tout lui-même.
        Un RESETTING SANS verrou est un état résiduel post-crash/reboot :
        on restaure la connexion.
        """
        return (
            dongle.state == DongleState.RESETTING
            and self.reset_lock is not None
            and self.reset_lock.is_locked(str(dongle_id))
        )

    async def reconcile_dongle(self, dongle_id: UUID) -> None:
        """Restaure un dongle spécifique : connexion, routes, proxy."""
        dongle = await self.dongle_repo.get_by_id(dongle_id)
        if dongle is None:
            logger.warning("Dongle %s introuvable, skip", dongle_id)
            return

        if self._reset_in_progress(dongle, dongle_id):
            logger.info("Reconciliation dongle %s sautée : reset en cours", dongle_id)
            return

        # Récupération douce : un dongle bloqué (ERROR/OFFLINE) avec un
        # bearer PPP (aucun netdev) ne peut pas se rétablir en reconnectant ;
        # on tente une ré-énumération USB bornée avant la restauration.
        await self.adb_soft_recovery.attempt_soft_recovery(dongle)

        dongle = await self.restoration.restore_connection_if_needed(dongle)
        dongle = await self.adb_soft_recovery.refresh_interface(dongle)

        # (Re)pose des routes d'uplink SANS reconnexion AVANT le démarrage du
        # conteneur proxy : la route réelle ou le plancher de la table dédiée
        # doit être stabilisé d'abord. Sur un dongle déjà actif (connect()
        # sauté), une route écrasée est détectée et réparée ici.
        await self._repair_uplink_routes(dongle)
        await self.proxy_reconciler.restore_dongle_proxy(dongle)
        await self._verify_connectivity(dongle)

    async def _repair_uplink_routes(self, dongle) -> None:
        """(Re)pose les routes d'uplink du dongle (délégation à la restauration)."""
        await self.restoration.repair_uplink_routes(dongle)

    async def _verify_connectivity(self, dongle) -> None:
        """Vérifie la connectivité et met à jour l'état (délégation à la restauration)."""
        await self.restoration.verify_connectivity(dongle)
