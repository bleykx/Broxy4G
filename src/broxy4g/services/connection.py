import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from broxy4g.adapters.connectivity import ConnectivityAdapter, PingResult
from broxy4g.adapters.modem_manager import ModemInfo, ModemManagerAdapter, SignalQuality
from broxy4g.adapters.net_interface import InterfaceAdapter
from broxy4g.adapters.nftables import NftablesAdapter
from broxy4g.adapters.routing import RoutingAdapter
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.interface_naming import NamedDongle, plan_interface_names
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.network_isolation import NetworkIsolationService
from broxy4g.services.routing_allocator import RoutingAllocator
from broxy4g.services.uplink_probe import UplinkProbe
from broxy4g.services.uplink_routes import UplinkRouteService

__all__ = ["ConnectionService", "DongleMetrics"]

logger = logging.getLogger(__name__)


@dataclass
class DongleMetrics:
    signal_quality: SignalQuality | None = None
    ping: PingResult | None = None
    public_ip: str | None = None
    proxy_health: str | None = None


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
        self.probe = UplinkProbe(iface, connectivity)
        self._ip_wait_attempts = ip_wait_attempts
        self._ip_wait_delay = ip_wait_delay
        self._modem_wait_attempts = modem_wait_attempts
        self._modem_wait_delay = modem_wait_delay

    async def _apply_isolation(self) -> None:
        if self.isolation is not None:
            await self.isolation.apply()

    async def _resolve_modem(self, dongle: Dongle) -> ModemInfo | None:
        """Resolve the modem by identity to handle USB port changes."""
        return await self.uplink_routes.resolve_modem(dongle)

    async def connect(self, dongle_id: UUID) -> Dongle:
        dongle = await self._load(dongle_id)
        dongle.change_state(DongleState.CONNECTING)

        # Resolve modem to handle USB port changes
        modem = await self._resolve_modem(dongle)
        if not modem or not modem.interface_name:
            logger.error(
                "Connect dongle %s échoué: modem introuvable ou sans interface (modem=%s)",
                dongle_id, modem,
            )
            dongle.fail(
                f"modem introuvable (IMEI={dongle.identity.imei}, "
                f"interface attendue={modem.interface_name if modem else None})"
            )
            await self.dongle_repo.save(dongle)
            return dongle

        await self._ensure_routing(dongle)

        try:
            # Renomme l'interface sur le nom dérivé du label (idempotent).
            # L'échec du renommage tombe dans l'except ci-dessous (dongle.fail).
            iface_name = await self._resolve_interface_name(dongle, modem.interface_name)
            dongle.interface_name = iface_name
            # ModemManager établit le bearer et expose la configuration IP ;
            # il ne configure jamais l'interface hôte : on l'applique nous-mêmes.
            await self.modem.connect(modem.path, dongle.apn or "", dongle.apn_auth)
            bearer = await self.modem.get_bearer_ip(modem.path)
            await self.iface.set_up(iface_name)
            # Purge les adresses résiduelles avant assignation : sinon une IPv4
            # périmée reste la 1re source et échappe à ``ip rule from <bearer>``.
            await self.iface.flush_ipv4(iface_name)
            if bearer and bearer.address:
                await self.iface.assign_address(iface_name, bearer.address, bearer.prefix)
            # (Re)pose idempotente des routes d'uplink (fwmark, default, source) :
            # seule implémentation, partagée avec la reconciliation.
            await self.uplink_routes.apply_bearer(dongle, bearer, iface_name)
            await self._wait_for_ip(iface_name, bearer.address if bearer else None)
        except Exception as exc:
            logger.exception(
                "Connect dongle %s échoué (modem=%s, interface=%s, apn=%s)",
                dongle_id, modem.path, modem.interface_name, dongle.apn,
            )
            detail = f"{type(exc).__name__}: {exc}"
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

    async def _resolve_interface_name(self, dongle: Dongle, current_name: str) -> str:
        """Renomme l'interface courante en le nom dérivé du label (idempotent).

        Le plan est déterministe sur l'ensemble des dongles (tri par
        identity_key) : aucun renvoi sur l'ordre USB ni le nom courant.
        """
        siblings = await self.dongle_repo.list_all()
        plan = plan_interface_names(
            NamedDongle(d.identity_key, d.label) for d in siblings
        )
        desired = plan[dongle.identity_key]
        if desired == current_name:
            return desired
        try:
            await self.iface.rename(current_name, desired)
        except Exception as exc:
            raise RuntimeError(
                f"échec renommage interface {current_name} -> {desired}: {exc}"
            ) from exc
        return desired

    async def _teardown_uplink(self, dongle: Dongle) -> None:
        """Déconnecte le bearer et nettoie interface + règles/routes.

        Ne change pas l'état du dongle, ne sauvegarde pas et n'applique pas
        l'isolation : ``disconnect`` (qui passe à OFFLINE) et ``reset_modem``
        (qui reste en RESETTING) partagent ce teardown.
        """
        # Resolve modem to handle USB port changes
        modem = await self._resolve_modem(dongle)
        if modem and modem.interface_name:
            await self.modem.disconnect(modem.path)
            await self.iface.flush_ipv4(modem.interface_name)
            await self.iface.set_down(modem.interface_name)
        elif dongle.interface_name:
            # Try to clean up even if modem is not found
            await self.iface.flush_ipv4(dongle.interface_name)
            await self.iface.set_down(dongle.interface_name)

        if dongle.route_table_id and dongle.fwmark:
            await self.routing.remove_rule(dongle.route_table_id, dongle.fwmark)
            await self.routing.remove_source_rule(dongle.route_table_id)
            await self.routing.remove_route(dongle.route_table_id, "default")

    async def disconnect(self, dongle_id: UUID) -> Dongle:
        dongle = await self._load(dongle_id)
        await self._teardown_uplink(dongle)
        dongle.change_state(DongleState.OFFLINE)
        await self.dongle_repo.save(dongle)
        await self._apply_isolation()
        return dongle

    async def reset_modem(self, dongle_id: UUID) -> Dongle:
        """Vrai reset modem USB (ré-énumération) puis reconnexion.

        Seul le reset modem change l'IP publique : disconnect+connect et même
        --delete-bearer ne suffisent pas. Le modem disparaît de ModemManager
        (~15-40 s) puis réapparaît, parfois sous un nouveau chemin et une
        interface renommée : il est retrouvé par identité (IMEI).

        L'état reste RESETTING pendant tout le reset (teardown sans passer par
        OFFLINE) : la réconciliation ignore donc le dongle tant que le verrou
        de reset est tenu.
        """
        dongle = await self._load(dongle_id)
        modem = await self._resolve_modem(dongle)
        if not modem:
            dongle.fail(f"modem introuvable (IMEI={dongle.identity.imei}) pour reset")
            await self.dongle_repo.save(dongle)
            return dongle

        try:
            await self._teardown_uplink(dongle)
            await self.modem.reset_modem(modem.path)
            # Phase 1 : attendre la disparition du modem (ré-énumération USB).
            for _ in range(self._modem_wait_attempts):
                if await self._resolve_modem(dongle) is None:
                    break
                await asyncio.sleep(self._modem_wait_delay)
            # Phase 2 : attendre la réapparition avec une interface exploitable.
            for _ in range(self._modem_wait_attempts):
                candidate = await self._resolve_modem(dongle)
                if candidate is not None and candidate.interface_name:
                    break
                await asyncio.sleep(self._modem_wait_delay)
            else:
                raise RuntimeError("modem non réapparu après reset")
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            dongle.fail(f"échec reset modem: {detail}")
            await self.dongle_repo.save(dongle)
            return dongle

        return await self.connect(dongle_id)

    async def get_metrics(self, dongle_id: UUID) -> DongleMetrics:
        dongle = await self._load(dongle_id)

        signal = None
        # Resolve modem to handle USB port changes
        modem = await self._resolve_modem(dongle)
        if modem:
            signal = await self.modem.get_signal(modem.path)

        ping = await self.probe.ping(dongle.interface_name)
        public_ip = await self.probe.public_ip(dongle.interface_name)

        return DongleMetrics(signal_quality=signal, ping=ping, public_ip=public_ip)

    async def get_public_ip(self, dongle_id: UUID) -> str | None:
        dongle = await self._load(dongle_id)
        return await self.probe.public_ip(dongle.interface_name)

    async def get_session_id(self, dongle_id: UUID) -> str | None:
        """Identifiant de la session data courante du dongle (None si inconnue)."""
        dongle = await self._load(dongle_id)
        modem = await self._resolve_modem(dongle)
        if not modem:
            return None
        return await self.modem.get_session_id(modem.path)

    async def _ensure_routing(self, dongle: Dongle) -> None:
        """Attribue (une fois) une table de routage et une marque firewall au dongle.

        L'allocation est déléguée à l'allocateur partagé (dongles + proxies),
        jamais à une vue partielle : c'est elle qui garantit l'unicité du slot.
        """
        if dongle.route_table_id and dongle.fwmark:
            return
        table_id, fwmark = await self.allocator.allocate()
        dongle.route_table_id = table_id
        dongle.fwmark = fwmark
        await self.dongle_repo.save(dongle)

    async def _wait_for_ip(self, interface: str, expected: str | None = None) -> str:
        """Poll iface.get_ip until the expected source IP appears or attempts run out.

        ``expected`` (l'IP du bearer) est exigée : une adresse résiduelle, même
        présente en 1re position, ne doit pas valider la connexion.
        """
        for _ in range(self._ip_wait_attempts):
            ip = await self.iface.get_ip(interface)
            if ip and (expected is None or ip == expected):
                return ip
            await asyncio.sleep(self._ip_wait_delay)
        raise RuntimeError(
            f"no IPv4 on {interface} after {self._ip_wait_attempts} attempts",
        )

    async def _load(self, dongle_id: UUID) -> Dongle:
        dongle = await self.dongle_repo.get_by_id(dongle_id)
        if not dongle:
            raise ValueError("Dongle not found")
        return dongle
