"""Écriture des artefacts de routage d'uplink d'une table dédiée.

Composant bas-niveau chargé de (re)poser, de façon idempotente, les quatre
artefacts ``ip`` de la table dédiée d'un dongle : la règle ``fwmark``, la
route par défaut de la table, la règle source « from <IP bearer> » et le
plancher anti-fuite (``default unreachable``). Logique pure de routage :
l'appelant (``UplinkRouteService``) fournit le bearer et l'interface ; aucun
changement d'état du dongle n'a lieu ici.

Une table de routage dédiée ne doit JAMAIS rester sans route ``default`` :
sinon le noyau retombe sur la table ``main`` (route par défaut de l'hôte) et
le proxy du dongle fuit par eth. Quand aucune route réelle ne peut être posée
(interface inconnue, bearer sans IPv4, état non actif), le plancher (route
``default unreachable``) est posé à la place.
"""

import logging

from broxy4g.adapters.modem_manager import BearerIpConfig
from broxy4g.adapters.routing import RoutingAdapter, RoutingRule
from broxy4g.domain.dongle import Dongle

logger = logging.getLogger(__name__)


class UplinkRouteWriter:
    """(Re)pose idempotente des artefacts ip/fwmark d'une table dédiée d'uplink."""

    def __init__(self, routing: RoutingAdapter):
        self.routing = routing

    async def apply_bearer(
        self, dongle: Dongle, bearer: BearerIpConfig | None, interface_name: str | None
    ) -> None:
        """(Re)pose règle fwmark + route par défaut + règle source, de façon idempotente.

        N'écrit que ce qui est absent ou faux ; un appel sur un état déjà
        correct ne produit aucune écriture. Quand aucune route réelle ne
        peut être posée (interface inconnue, bearer absent ou sans IPv4),
        FORCE le plancher : une table dédiée ne doit jamais rester sans
        route ``default``, et un bearer perdu ne doit pas conserver
        l'ancienne route réelle.
        """
        if not (dongle.route_table_id and dongle.fwmark):
            return
        table_id = dongle.route_table_id
        rules = await self.routing.list_rules()
        await self._ensure_fwmark_rule(dongle, table_id, rules)
        if not interface_name or bearer is None or not bearer.address:
            # Aucune IPv4 à épingler (interface inconnue, session PPP sans
            # port net, ...) : plancher forcé, l'ancienne route réelle
            # n'a plus de source valide.
            await self.apply_barrier(dongle, force=True)
            return
        await self._apply_default_route(dongle, table_id, bearer, interface_name)
        await self._apply_source_rule(dongle, table_id, rules, bearer)

    async def apply_barrier(self, dongle: Dongle, *, force: bool = False) -> None:
        """Assure qu'une route ``default`` existe dans la table dédiée (plancher).

        S'assurer qu'une route ``default`` existe : si elle est déjà là
        (route RÉELLE ou plancher), rien n'est écrit — une route existante
        n'est jamais dégradée. ``force=True`` remplace en revanche la route
        ``default`` existante par le plancher (``default unreachable``),
        pour un dongle qui vient de perdre son bearer/état actif.
        No-op si aucun slot de routage (table/fwmark) alloué.
        """
        if not (dongle.route_table_id and dongle.fwmark):
            return
        table_id = dongle.route_table_id
        rules = await self.routing.list_rules()
        await self._ensure_fwmark_rule(dongle, table_id, rules)
        routes = await self.routing.list_routes(table_id)
        if not force and any(r.dest == "default" for r in routes):
            return
        logger.info(
            "Dongle %s : route par défaut %s de la table %d, plancher posé (unreachable)",
            dongle.id,
            "absente" if not force else "existante remplacée (forcé)",
            table_id,
        )
        await self.routing.add_barrier_route(table_id)

    async def _ensure_fwmark_rule(
        self, dongle: Dongle, table_id: int, rules: list[RoutingRule]
    ) -> None:
        """(Re)pose la règle fwmark si elle est absente des règles listées."""
        if any(r.table_id == table_id and r.fwmark == dongle.fwmark for r in rules):
            return
        logger.info(
            "Dongle %s : règle fwmark %s (table %d) absente, re-posée",
            dongle.id, dongle.fwmark, table_id,
        )
        await self.routing.add_rule(table_id, dongle.fwmark)

    async def _apply_default_route(
        self, dongle: Dongle, table_id: int, bearer: BearerIpConfig, interface_name: str
    ) -> None:
        """Route par défaut de la table dédiée ; sans passerelle, fallback scope link.

        L'idempotence compare la route RÉELLE (type ``"unicast"``) : un
        plancher présent dans la table est remplacé par la route réelle.
        """
        desired_via = bearer.gateway or None
        routes = await self.routing.list_routes(table_id)
        existing = next((r for r in routes if r.dest == "default" and r.type == "unicast"), None)
        if existing is not None and existing.dev == interface_name and existing.via == desired_via:
            return
        if desired_via is None:
            logger.warning(
                "Dongle %s : bearer sans passerelle — route posée sans via "
                "('default dev %s', scope link) dans la table %d ; le trafic sort "
                "probablement mal tant qu'aucun via n'est disponible",
                dongle.id, interface_name, table_id,
            )
        elif existing is not None:
            logger.info(
                "Dongle %s : route par défaut de la table %d réparée (dev=%s via=%s -> dev=%s via=%s)",
                dongle.id, table_id, existing.dev, existing.via, interface_name, desired_via,
            )
        elif any(r.dest == "default" and r.type != "unicast" for r in routes):
            logger.info(
                "Dongle %s : plancher de la table %d remplacé par la route réelle (dev=%s via=%s)",
                dongle.id, table_id, interface_name, desired_via,
            )
        else:
            logger.info(
                "Dongle %s : route par défaut absente de la table %d, posée (dev=%s via=%s)",
                dongle.id, table_id, interface_name, desired_via,
            )
        await self.routing.add_route(table_id, "default", desired_via, interface_name)

    async def _apply_source_rule(
        self, dongle: Dongle, table_id: int, rules: list[RoutingRule], bearer: BearerIpConfig
    ) -> None:
        """Règle « from <IP bearer> » : le trafic du dongle sort par son uplink."""
        if not bearer.address:
            return
        existing = next(
            (r for r in rules if r.table_id == table_id and r.source is not None), None
        )
        if existing is not None and existing.source == bearer.address:
            return
        logger.info(
            "Dongle %s : règle source table %d réparée (%s -> %s)",
            dongle.id, table_id,
            existing.source if existing is not None else None, bearer.address,
        )
        await self.routing.add_source_rule(bearer.address, table_id)
