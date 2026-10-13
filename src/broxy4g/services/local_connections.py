"""Connexions locales : interfaces hôte pouvant porter un proxy 3proxy.

Une connexion locale est une interface réseau de l'hôte (ethernet, wifi, etc.)
distincte des dongles gérés. Enrôler une connexion crée un proxy dédié et
l'épingle strictement à cette interface via une table de routage et une marque
firewall dédiées (le trafic ne peut sortir que par cette interface).
"""

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from broxy4g.adapters.local_uplink import LocalUplinkAdapter
from broxy4g.adapters.routing import RoutingAdapter
from broxy4g.domain.proxy import Proxy, ProxySource
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.network_isolation import NetworkIsolationService
from broxy4g.services.proxy_health import ProxyHealth, ProxyHealthService
from broxy4g.services.proxy_lifecycle import ProxyLifecycleService
from broxy4g.services.proxy_provisioning import ProvisionedProxy
from broxy4g.services.routing_allocator import RoutingAllocator

logger = logging.getLogger(__name__)


class LocalConnectionNotFound(ValueError):
    """Interface inconnue ou absente de l'hôte."""


@dataclass(frozen=True)
class LocalConnection:
    interface_name: str
    ip: str | None
    kind: str
    enrolled: bool
    proxy_id: UUID | None
    gateway: str | None
    link_state: str  # "up" | "down" | "unknown"


@dataclass(frozen=True)
class LocalConnectionDetail:
    interface_name: str
    ip: str | None
    kind: str
    gateway: str | None
    link_state: str
    dns: list[str] | None
    enrolled: bool
    proxy: Proxy | None
    proxy_health: ProxyHealth | None


class LocalConnectionsService:
    def __init__(
        self,
        uplink: LocalUplinkAdapter,
        proxy_repo: ProxyRepository,
        proxy_service: ProxyLifecycleService,
        routing: RoutingAdapter,
        dongle_repo: DongleRepository,
        allocator: RoutingAllocator,
        isolation: NetworkIsolationService,
        proxy_health: ProxyHealthService | None = None,
    ):
        self.uplink = uplink
        self.proxy_repo = proxy_repo
        self.proxy_service = proxy_service
        self.routing = routing
        self.dongle_repo = dongle_repo
        self.allocator = allocator
        self.isolation = isolation
        self.proxy_health = proxy_health
        self._lock = asyncio.Lock()

    async def list_all(self) -> list[LocalConnection]:
        dongles = await self.dongle_repo.list_all()
        dongle_ifaces = {d.interface_name for d in dongles if d.interface_name}
        connections: list[LocalConnection] = []
        for iface in await self.uplink.list_interfaces():
            if iface.name in dongle_ifaces:
                continue
            proxy = await self.proxy_repo.get_by_source(ProxySource.LOCAL, iface.name)
            connections.append(
                LocalConnection(
                    interface_name=iface.name,
                    ip=iface.ip,
                    kind=iface.kind,
                    gateway=await self.uplink.default_gateway(iface.name),
                    link_state=await self.uplink.link_state(iface.name),
                    enrolled=proxy is not None,
                    proxy_id=proxy.id if proxy else None,
                )
            )
        return connections

    async def get(self, interface_name: str) -> LocalConnectionDetail:
        """Vue détaillée d'une interface (lien, DNS, proxy et sa santé)."""
        interfaces = {i.name: i for i in await self.uplink.list_interfaces()}
        iface = interfaces.get(interface_name)
        if iface is None:
            raise LocalConnectionNotFound(f"interface inconnue: {interface_name}")
        proxy = await self.proxy_repo.get_by_source(ProxySource.LOCAL, interface_name)
        return LocalConnectionDetail(
            interface_name=iface.name,
            ip=iface.ip,
            kind=iface.kind,
            gateway=await self.uplink.default_gateway(interface_name),
            link_state=await self.uplink.link_state(interface_name),
            dns=await self.uplink.dns(interface_name),
            enrolled=proxy is not None,
            proxy=proxy,
            proxy_health=await self._health_of(proxy),
        )

    async def _health_of(self, proxy: Proxy | None) -> ProxyHealth | None:
        if proxy is None:
            return None
        if self.proxy_health is None:
            return ProxyHealth("unknown", "santé non calculée")
        return await self.proxy_health.for_proxy(proxy)

    async def enroll(self, interface_name: str, *, enabled: bool = True) -> ProvisionedProxy:
        """Enrôle une interface hôte : crée son proxy et l'épingle à l'interface."""
        interfaces = {i.name: i for i in await self.uplink.list_interfaces()}
        if interface_name not in interfaces:
            raise ValueError(f"interface inconnue: {interface_name}")
        gateway = await self.uplink.default_gateway(interface_name)
        if not gateway:
            raise ValueError(f"interface {interface_name} sans passerelle IPv4")
        provisioned = await self.proxy_service.create_local(interface_name, enabled=enabled)
        await self._pin(provisioned.proxy, gateway, interface_name)
        await self.isolation.apply()
        return provisioned

    async def unenroll(self, interface_name: str) -> None:
        """Retire le proxy d'une interface et ses règles de routage."""
        proxy = await self.proxy_repo.get_by_source(ProxySource.LOCAL, interface_name)
        if proxy is None:
            return
        await self.proxy_service.remove(proxy.id)

    async def restore(self, interface_name: str) -> None:
        """Ré-applique l'épinglage d'un proxy local après reboot (idempotent).

        Si l'interface est absente (wifi coupé, matériel retiré), on ne
        supprime rien : le proxy reste configuré et sera ré-épinglé au retour.
        """
        proxy = await self.proxy_repo.get_by_source(ProxySource.LOCAL, interface_name)
        if proxy is None:
            return
        if interface_name not in {i.name for i in await self.uplink.list_interfaces()}:
            logger.warning("Interface %s absente, épingle non restaurée", interface_name)
            return
        gateway = await self.uplink.default_gateway(interface_name)
        if not gateway:
            logger.warning("Interface %s sans passerelle, épingle non restaurée", interface_name)
            return
        await self._pin(proxy, gateway, interface_name)
        await self.isolation.apply()

    async def _pin(self, proxy: Proxy, gateway: str, interface_name: str) -> None:
        # Le verrou sérialise la lecture des slots et l'écriture des règles ;
        # l'allocation elle-même est déléguée à l'allocateur partagé.
        async with self._lock:
            if not proxy.route_table_id or not proxy.fwmark:
                table_id, fwmark = await self.allocator.allocate()
                proxy.route_table_id = table_id
                proxy.fwmark = fwmark
                await self.proxy_repo.save(proxy)
            await self.routing.add_rule(proxy.route_table_id, proxy.fwmark)
            await self.routing.add_route(
                proxy.route_table_id, "default", gateway, interface_name
            )
