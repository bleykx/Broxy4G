"""Allocation unique des slots de routage (table dédiée + marque firewall).

Un seul composant, partagé par le service de connexion dongle et le service
des connexions locales, évite que deux uplinks distincts reçoivent la même
table de routage / le même fwmark. Le verrou sérialise les read-modify-write
concurrents : l'allocation se base sur l'état persisté (dongles + proxies),
jamais sur une vue partielle.
"""

import asyncio

from broxy4g.domain.routing_slots import allocate_routing_slot
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository


class RoutingAllocator:
    """Attribue un slot (route_table_id, fwmark) libre pour n'importe quel uplink."""

    def __init__(
        self,
        dongle_repo: DongleRepository,
        proxy_repo: ProxyRepository,
    ):
        self.dongle_repo = dongle_repo
        self.proxy_repo = proxy_repo
        self._lock = asyncio.Lock()

    async def allocate(self) -> tuple[int, int]:
        """Retourne (route_table_id, fwmark) non attribués à aucun dongle ni proxy."""
        async with self._lock:
            dongles = await self.dongle_repo.list_all()
            proxies = await self.proxy_repo.list_all()
            used = [d.route_table_id for d in dongles if d.route_table_id is not None]
            used += [p.route_table_id for p in proxies if p.route_table_id is not None]
            return allocate_routing_slot(used)
