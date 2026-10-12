"""Réparation des doublons de slots de routage persistés en base.

Un bug d'allocation antérieur pouvait attribuer le même couple
``(route_table_id, fwmark)`` à un dongle et à un proxy local : deux
uplinks distincts écrivaient alors la même table de routage et
partageaient la même marque firewall d'isolation. Cette migration
répare uniquement la base de données :

- le slot reste au dongle s'il y en a un (sa table porte sa règle
  source ``from <IP bearer>``), sinon au proxy local au plus petit
  ``listen_port`` (deterministe) ;
- les autres proxies locaux perdent leur slot (``None``) et seront
  ré-épinglés par le chemin existant (``LocalConnectionsService.restore``
  → allocateur unifié) ;
- un dongle n'est JAMAIS modifié, de même qu'un proxy non local.

Aucune règle ni route noyau n'est supprimée ici : la règle ``ip rule``
du dongle et celle du proxy étaient littéralement identiques, et la
route polluante de la table du dongle est réparée par la
reconciliation (``UplinkRouteService``).
"""

import logging
from collections import defaultdict

from broxy4g.domain.dongle import Dongle
from broxy4g.domain.proxy import Proxy, ProxySource
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository

logger = logging.getLogger(__name__)


class RoutingSlotMigration:
    """Répare les doublons de slots ``(route_table_id, fwmark)`` persistés."""

    def __init__(self, dongle_repo: DongleRepository, proxy_repo: ProxyRepository):
        self.dongle_repo = dongle_repo
        self.proxy_repo = proxy_repo

    async def repair_duplicates(self) -> None:
        """Libère les proxies locaux en double sur un slot (idempotent).

        N'effectue aucune écriture en base s'il n'y a aucun conflit.
        """
        dongles = await self.dongle_repo.list_all()
        proxies = await self.proxy_repo.list_all()
        groups = self._conflicting_groups(dongles, proxies)
        for (table_id, fwmark), group in groups.items():
            await self._resolve_group(table_id, fwmark, group)

    @staticmethod
    def _conflicting_groups(dongles: list[Dongle], proxies: list[Proxy]) -> dict[tuple[int, int], list]:
        """Regroupe les entités par slot complet ``(table, fwmark)`` (>= 2 membres)."""
        groups: dict[tuple[int, int], list] = defaultdict(list)
        for dongle in dongles:
            if dongle.route_table_id is not None and dongle.fwmark is not None:
                groups[(dongle.route_table_id, dongle.fwmark)].append(dongle)
        for proxy in proxies:
            if proxy.route_table_id is not None and proxy.fwmark is not None:
                groups[(proxy.route_table_id, proxy.fwmark)].append(proxy)
        return {slot: group for slot, group in groups.items() if len(group) > 1}

    async def _resolve_group(self, table_id: int, fwmark: int, group: list) -> None:
        """Rétablit un seul propriétaire pour le slot ; libère les proxies locaux."""
        dongles = [e for e in group if isinstance(e, Dongle)]
        local_proxies: list[Proxy] = []
        unresolvable: list[Proxy] = []
        for proxy in group:
            if isinstance(proxy, Dongle):
                continue
            if proxy.source_type is ProxySource.LOCAL:
                local_proxies.append(proxy)
            else:
                unresolvable.append(proxy)
        if len(dongles) >= 2:
            logger.warning(
                "Slot (table=%d fwmark=%d) occupé par %d dongles : aucun changement",
                table_id, fwmark, len(dongles),
            )
            return
        for proxy in unresolvable:
            logger.warning(
                "Slot (table=%d fwmark=%d) : proxy non local %s en conflit, non modifié",
                table_id, fwmark, proxy.id,
            )
        owner = self._owner(dongles, local_proxies)
        if owner is None:
            return
        released = (
            local_proxies
            if isinstance(owner, Dongle)
            else [p for p in local_proxies if p is not owner]
        )
        for proxy in released:
            await self._release(proxy, owner)

    async def _release(self, proxy: Proxy, owner: Dongle | Proxy) -> None:
        """Libère le slot d'un proxy local et journalise la raison."""
        old_table, old_fwmark = proxy.route_table_id, proxy.fwmark
        proxy.route_table_id = None
        proxy.fwmark = None
        await self.proxy_repo.save(proxy)
        logger.info(
            "Proxy local %s libéré du slot (table=%d fwmark=%d) : conflit avec %s",
            proxy.id, old_table, old_fwmark, self._owner_label(owner),
        )

    @staticmethod
    def _owner(dongles: list[Dongle], local_proxies: list[Proxy]) -> Dongle | Proxy | None:
        """Le dongle prioritaire, sinon le proxy local au plus petit port."""
        if dongles:
            return dongles[0]
        if local_proxies:
            return min(local_proxies, key=lambda p: p.listen_port)
        return None

    @staticmethod
    def _owner_label(owner: Dongle | Proxy) -> str:
        if isinstance(owner, Dongle):
            return f"le dongle {owner.id}"
        return f"le proxy local {owner.id}"
