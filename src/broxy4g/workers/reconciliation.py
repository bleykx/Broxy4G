import logging
from uuid import UUID

from broxy4g.adapters.connectivity import ConnectivityAdapter
from broxy4g.adapters.docker_compose import DockerComposeAdapter
from broxy4g.adapters.modem_manager import ModemManagerAdapter
from broxy4g.adapters.nftables import NftablesAdapter
from broxy4g.adapters.routing import RoutingAdapter
from broxy4g.domain.dongle_states import DongleState, can_transition
from broxy4g.domain.reset_states import ResetLock
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.connection import ConnectionService
from broxy4g.services.local_connections import LocalConnectionsService
from broxy4g.services.metrics_snapshot import MetricsSnapshot
from broxy4g.services.network_isolation import NetworkIsolationService
from broxy4g.services.proxy_provisioning import ProxyProvisioningService
from broxy4g.services.routing_slot_migration import RoutingSlotMigration
from broxy4g.services.uplink_routes import UplinkRouteService
from broxy4g.services.usb_recovery import UsbRecoveryService
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
    ):
        self.dongle_repo = dongle_repo
        self.proxy_repo = proxy_repo
        self.modem = modem
        self.routing = routing
        self.nft = nft
        self.connectivity = connectivity
        self.connection_service = connection_service
        self.provisioning = provisioning
        self.docker_compose = docker_compose
        self.metrics_snapshot = metrics_snapshot
        self.uplink_routes = uplink_routes
        self.slot_migration = slot_migration
        self.isolation = isolation
        self.reset_lock = reset_lock
        self.local_connections = local_connections
        self.usb_recovery = usb_recovery
        self.proxy_reconciler = ProxyReconciler(
            proxy_repo=proxy_repo,
            dongle_repo=dongle_repo,
            provisioning=provisioning,
            docker_compose=docker_compose,
            local_connections=local_connections,
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
        await self.reconcile_proxies()
        await self._reapply_isolation()

    async def _repair_slot_duplicates(self) -> None:
        """Répare les doublons de slots persistés avant toute restauration.

        Un échec de la migration ne doit pas empêcher le reste du cycle.
        """
        try:
            await self.slot_migration.repair_duplicates()
        except Exception:
            logger.exception("Échec réparation doublons de slots de routage")

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

    async def reconcile_dongle(self, dongle_id: UUID) -> None:
        """Restaure un dongle spécifique : connexion, routes, proxy."""
        dongle = await self.dongle_repo.get_by_id(dongle_id)
        if dongle is None:
            logger.warning("Dongle %s introuvable, skip", dongle_id)
            return

        # Un reset réellement en cours (RESETTING ET verrou tenu) est totalement
        # ignoré : connexion, proxy ET connectivité. Le reset gère tout lui-même.
        # Un RESETTING SANS verrou est un état résiduel post-crash/reboot :
        # on restaure la connexion.
        reset_in_progress = (
            dongle.state == DongleState.RESETTING
            and self.reset_lock is not None
            and self.reset_lock.is_locked(str(dongle_id))
        )
        if reset_in_progress:
            logger.info(
                "Reconciliation dongle %s sautée : reset en cours", dongle_id
            )
            return

        # Récupération douce : un dongle bloqué (ERROR/OFFLINE) avec un
        # bearer PPP (aucun netdev) ne peut pas se rétablir en reconnectant ;
        # on tente une ré-énumération USB bornée avant la restauration.
        await self._attempt_usb_recovery(dongle)

        # Ne PAS reconnecter si le dongle est déjà actif.
        skip_connect = dongle.state in (DongleState.ONLINE, DongleState.CONNECTING)

        if not skip_connect:
            logger.info("Reconciliation dongle %s (état=%s)", dongle_id, dongle.state.value)
            await self._restore_connection(dongle)
            # connection_service.connect() charge et persiste sa propre copie ;
            # recharger pour que la vérification voie le nouvel état.
            refreshed = await self.dongle_repo.get_by_id(dongle_id)
            if refreshed is not None:
                dongle = refreshed

        await self.proxy_reconciler.restore_dongle_proxy(dongle)
        # (Re)pose des routes d'uplink SANS reconnexion : sur un dongle déjà
        # actif (connect() sauté), une route écrasée est détectée et réparée ici.
        await self._repair_uplink_routes(dongle)
        await self._verify_connectivity(dongle)

    async def _attempt_usb_recovery(self, dongle) -> None:
        """Délègue la récupération USB douce ; un échec ne casse pas le cycle."""
        if self.usb_recovery is None:
            return
        try:
            await self.usb_recovery.attempt(dongle.id)
        except Exception:
            logger.exception("Échec récupération USB douce dongle %s", dongle.id)

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

    async def _repair_uplink_routes(self, dongle) -> None:
        """(Re)pose les routes d'uplink du dongle sans reconnexion du modem.

        Un échec de cette étape ne doit pas faire échouer tout le cycle.
        """
        try:
            await self.uplink_routes.apply_for_dongle(dongle)
        except Exception:
            logger.exception("Échec réparation routes uplink dongle %s", dongle.id)

    async def _verify_connectivity(self, dongle) -> None:
        """Vérifie la connectivité et met à jour l'état."""
        try:
            metrics = await self.connection_service.get_metrics(dongle.id)
            signal = metrics.signal_quality.percent if metrics.signal_quality else None
            self.metrics_snapshot.update(dongle.id, metrics.public_ip, signal)
            target = DongleState.DEGRADED if metrics.public_ip is None else DongleState.ONLINE
            if dongle.state != target and can_transition(dongle.state, target):
                dongle.change_state(target)
                await self.dongle_repo.save(dongle)
        except Exception:
            logger.exception("Vérification connectivité échouée pour %s", dongle.id)
