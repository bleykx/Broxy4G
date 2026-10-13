import logging
from uuid import UUID

from broxy4g.adapters.connectivity import ConnectivityAdapter
from broxy4g.adapters.modem_manager import ModemInfo, ModemManagerAdapter
from broxy4g.adapters.net_interface import InterfaceAdapter
from broxy4g.adapters.nftables import NftablesAdapter
from broxy4g.adapters.routing import RoutingAdapter
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.dongle_diagnostics import DongleDiagnostics, DongleMetrics
from broxy4g.services.network_isolation import NetworkIsolationService
from broxy4g.services.routing_allocator import RoutingAllocator
from broxy4g.services.uplink_routes import UplinkRouteService
from broxy4g.services.uplink_session import UplinkSession

__all__ = ["ConnectionService", "DongleMetrics"]

logger = logging.getLogger(__name__)


class ConnectionService:
    def __init__(
        self,
        dongle_repo: DongleRepository,
        modem: ModemManagerAdapter,
        iface: InterfaceAdapter,
        routing: RoutingAdapter,
        nft: NftablesAdapter,
        connectivity: ConnectivityAdapter,
        allocator: RoutingAllocator,
        uplink_routes: UplinkRouteService,
        isolation: NetworkIsolationService | None = None,
        ip_wait_attempts: int = 10,
        ip_wait_delay: float = 2.0,
        modem_wait_attempts: int = 60,
        modem_wait_delay: float = 1.5,
    ):
        self.dongle_repo = dongle_repo
        self.modem = modem
        self.iface = iface
        self.routing = routing
        self.nft = nft
        self.connectivity = connectivity
        self.allocator = allocator
        self.uplink_routes = uplink_routes
        self.isolation = isolation
        self.diagnostics = DongleDiagnostics(dongle_repo, modem, iface, connectivity, uplink_routes)
        self.uplink_session = UplinkSession(
            modem, iface, routing, uplink_routes,
            ip_wait_attempts=ip_wait_attempts, ip_wait_delay=ip_wait_delay,
            modem_wait_attempts=modem_wait_attempts, modem_wait_delay=modem_wait_delay,
        )

    async def _apply_isolation(self) -> None:
        if self.isolation is not None:
            await self.isolation.apply()

    async def _seal_failure(self, dongle: Dongle) -> None:
        """Scelle l'échec : plancher (si slot) + isolation avant toute coupure.

        Garde-fou ``route_table_id`` : chemin parcouru aussi AVANT ``_ensure_routing``.
        """
        if dongle.route_table_id:
            await self.routing.add_barrier_route(dongle.route_table_id)
        await self._apply_isolation()

    async def connect(self, dongle_id: UUID) -> Dongle:
        dongle = await self._load(dongle_id)
        dongle.change_state(DongleState.CONNECTING)

        # Resolve modem to handle USB port changes
        modem = await self._resolve_modem(dongle)
        if modem is None:
            return await self._fail_connect(dongle, dongle_id, f"modem introuvable (IMEI={dongle.identity.imei})")
        if modem.interface_name is None:
            return await self._fail_connect(dongle, dongle_id, "modem sans interface réseau (port net absent chez ModemManager)")

        # Ne jamais renommer un netdev (fragile avec ModemManager : le port net
        # du modem serait perdu). Le nom provient du netdev rattaché au modem (sysfs), persisté.
        iface_name = modem.interface_name
        dongle.interface_name = iface_name

        await self._ensure_routing(dongle)

        try:
            # ModemManager établit le bearer et expose la configuration IP ;
            # il ne configure jamais l'interface hôte : on l'applique nous-mêmes.
            await self.modem.connect(modem.path, dongle.apn or "", dongle.apn_auth)
            bearer = await self.modem.get_bearer_ip(modem.path)
            if bearer is not None and bearer.method == "ppp":
                return await self._fail_connect(
                    dongle, dongle_id,
                    "ModemManager a établi une session PPP sans interface réseau "
                    "(port net absent chez ModemManager)",
                )
            await self.iface.set_up(iface_name)
            # Purge les adresses résiduelles avant assignation : sinon une IPv4
            # périmée reste la 1re source et échappe à ``ip rule from <bearer>``.
            await self.iface.flush_ipv4(iface_name)
            if bearer and bearer.address:
                await self.iface.assign_address(iface_name, bearer.address, bearer.prefix)
            # (Re)pose idempotente des routes d'uplink (fwmark, default, source) :
            # seule implémentation, partagée avec la reconciliation.
            await self.uplink_routes.apply_bearer(dongle, bearer, iface_name)
            await self.uplink_session.wait_for_ip(iface_name, bearer.address if bearer else None)
        except Exception as exc:
            logger.exception(
                "Connect dongle %s échoué (modem=%s, interface=%s, apn=%s)",
                dongle_id, modem.path, modem.interface_name, dongle.apn,
            )
            detail = f"{type(exc).__name__}: {exc}"
            await self._seal_failure(dongle)
            dongle.fail(
                f"échec connexion (apn={dongle.apn!r}, interface={modem.interface_name}, "
                f"modem={modem.path}): {detail}"
            )
            await self.dongle_repo.save(dongle)
            return dongle

        dongle.clear_error()
        dongle.change_state(DongleState.ONLINE)
        await self.dongle_repo.save(dongle)
        await self._apply_isolation()
        return dongle

    async def _fail_connect(self, dongle: Dongle, dongle_id: UUID, message: str) -> Dongle:
        """Échec métier : log ERROR lisible + dongle ERROR."""
        logger.error("Connect dongle %s échoué: %s", dongle_id, message)
        await self._seal_failure(dongle)
        dongle.fail(message)
        await self.dongle_repo.save(dongle)
        return dongle

    async def disconnect(self, dongle_id: UUID) -> Dongle:
        dongle = await self._load(dongle_id)
        await self.uplink_session.teardown(dongle)
        dongle.change_state(DongleState.OFFLINE)
        await self.dongle_repo.save(dongle)
        await self._apply_isolation()
        return dongle

    async def reset_modem(self, dongle_id: UUID) -> Dongle:
        """Vrai reset modem USB (ré-énumération) puis reconnexion.

        Seul le reset modem change l'IP publique ; l'état reste RESETTING pendant
        tout le reset (teardown sans OFFLINE, verrou de reset tenu).
        """
        dongle = await self._load(dongle_id)
        modem = await self._resolve_modem(dongle)
        if not modem:
            dongle.fail(f"modem introuvable (IMEI={dongle.identity.imei}) pour reset")
            await self.dongle_repo.save(dongle)
            return dongle

        try:
            await self.uplink_session.teardown(dongle)
            await self.modem.reset_modem(modem.path)
            await self.uplink_session.wait_reenumeration(dongle)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            await self._seal_failure(dongle)
            dongle.fail(f"échec reset modem: {detail}")
            await self.dongle_repo.save(dongle)
            return dongle

        return await self.connect(dongle_id)

    async def get_metrics(self, dongle_id: UUID) -> DongleMetrics:
        return await self.diagnostics.get_metrics(dongle_id)

    async def get_public_ip(self, dongle_id: UUID) -> str | None:
        return await self.diagnostics.get_public_ip(dongle_id)

    async def get_session_id(self, dongle_id: UUID) -> str | None:
        return await self.diagnostics.get_session_id(dongle_id)

    async def _resolve_modem(self, dongle: Dongle) -> ModemInfo | None:
        return await self.uplink_routes.resolve_modem(dongle)

    async def _ensure_routing(self, dongle: Dongle) -> None:
        """Attribue (une fois) table de routage + fwmark au dongle.

        L'allocateur partagé (dongles + proxies) garantit l'unicité du slot.
        """
        if dongle.route_table_id and dongle.fwmark:
            return
        table_id, fwmark = await self.allocator.allocate()
        dongle.route_table_id = table_id
        dongle.fwmark = fwmark
        await self.dongle_repo.save(dongle)
        # Plancher anti-fuite dès l'allocation (default unreachable).
        await self.routing.add_barrier_route(table_id)

    async def _load(self, dongle_id: UUID) -> Dongle:
        dongle = await self.dongle_repo.get_by_id(dongle_id)
        if not dongle:
            raise ValueError("Dongle not found")
        return dongle
