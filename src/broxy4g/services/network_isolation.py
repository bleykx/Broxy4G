"""Application du ruleset d'isolation réseau à partir de l'état persisté.

Le service lit les dongles et proxys en base, construit la liste des rattachements
proxy → uplink, puis applique (ou vide) la table nftables dédiée. Idempotent :
peut être rappelé après chaque changement ou périodiquement par la réconciliation.
"""

import asyncio
import logging

from broxy4g.adapters.nftables import NftablesAdapter
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.proxy import ProxySource
from broxy4g.domain.proxy_isolation import ProxyUplink, build_isolation_ruleset
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository

logger = logging.getLogger(__name__)

# États pour lesquels l'interface du dongle est considérée comme sortante active.
ACTIVE_INTERFACE_STATES = frozenset(
    {DongleState.ONLINE, DongleState.DEGRADED, DongleState.CONNECTING}
)


class NetworkIsolationService:
    def __init__(
        self,
        proxy_repo: ProxyRepository,
        dongle_repo: DongleRepository,
        nft: NftablesAdapter,
    ):
        self.proxy_repo = proxy_repo
        self.dongle_repo = dongle_repo
        self.nft = nft
        self._lock = asyncio.Lock()

    async def bindings(self) -> list[ProxyUplink]:
        """Rattachements proxy → uplink déduits de l'état persisté."""
        dongles = {d.id: d for d in await self.dongle_repo.list_all()}
        result: list[ProxyUplink] = []
        for proxy in await self.proxy_repo.list_all():
            if not proxy.enabled or not proxy.network_ip:
                continue
            if proxy.source_type is ProxySource.LOCAL:
                # Uplink local (interface hôte) : pas d'état dongle, l'interface
                # est active tant que le proxy est activé et épinglé.
                if proxy.fwmark and proxy.interface_name:
                    result.append(
                        ProxyUplink(proxy.network_ip, proxy.fwmark, proxy.interface_name)
                    )
                continue
            if proxy.dongle_id is None:
                continue
            dongle = dongles.get(proxy.dongle_id)
            if dongle is None or not dongle.fwmark:
                continue
            interface = (
                dongle.interface_name
                if dongle.state in ACTIVE_INTERFACE_STATES
                else None
            )
            result.append(ProxyUplink(proxy.network_ip, dongle.fwmark, interface))
        return result

    async def apply(self) -> None:
        """Applique le ruleset d'isolation correspondant à l'état courant.

        Le snapshot (bindings) et l'écriture nftables sont dans la même section
        critique : un apply() concurrent ne s'entrelace jamais (single-flight).
        """
        async with self._lock:
            bindings = await self.bindings()
            await self.nft.apply_ruleset(build_isolation_ruleset(bindings))
            logger.debug("Isolation réseau appliquée pour %d proxy(s)", len(bindings))

    async def clear(self) -> None:
        """Vide la table d'isolation (aucun proxy rattaché)."""
        async with self._lock:
            await self.nft.apply_ruleset(build_isolation_ruleset([]))
