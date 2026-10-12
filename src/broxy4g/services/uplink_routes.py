"""(Re)pose idempotente des routes d'uplink d'un dongle à partir de son bearer.

Seule implémentation des artefacts de routage d'uplink (règle fwmark,
route par défaut de la table dédiée, règle source « from <IP bearer> ») :
réutilisée par ``ConnectionService.connect`` et par la reconciliation, qui
détecte ainsi une route écrasée sur un dongle déjà actif (ONLINE) sans
reconnexion du modem. Aucun changement d'état du dongle ici.
"""

import logging

from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo, ModemManagerAdapter
from broxy4g.adapters.routing import RoutingAdapter, RoutingRule
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial

logger = logging.getLogger(__name__)


class UplinkRouteService:
    """Pose et répare les routes d'uplink d'un dongle, sans jamais reconnecter le modem."""

    def __init__(self, modem: ModemManagerAdapter, routing: RoutingAdapter):
        self.modem = modem
        self.routing = routing

    async def resolve_modem(self, dongle: Dongle) -> ModemInfo | None:
        """Résout le modem par identité matérielle (tolère un changement de port USB)."""
        for modem in await self.modem.list_modems():
            identity = IdentityMaterial(
                imei=modem.imei, usb_serial=modem.usb_serial, iccid=modem.iccid
            )
            if identity.matches(dongle.identity):
                return modem
        return None

    async def apply_for_dongle(self, dongle: Dongle) -> None:
        """(Re)pose les routes d'uplink depuis le bearer courant (chemin reconciliation)."""
        if not dongle.interface_name:
            logger.debug("Dongle %s sans interface connue, routes d'uplink non vérifiées", dongle.id)
            return
        modem = await self.resolve_modem(dongle)
        if modem is None:
            logger.warning("Dongle %s : modem introuvable, routes d'uplink non vérifiées", dongle.id)
            return
        bearer = await self.modem.get_bearer_ip(modem.path)
        await self.apply_bearer(dongle, bearer, dongle.interface_name)

    async def apply_bearer(
        self, dongle: Dongle, bearer: BearerIpConfig | None, interface_name: str | None
    ) -> None:
        """(Re)pose règle fwmark + route par défaut + règle source, de façon idempotente.

        N'écrit que ce qui est absent ou faux ; un appel sur un état déjà
        correct ne produit aucune écriture.
        """
        if not (dongle.route_table_id and dongle.fwmark and interface_name):
            return
        table_id = dongle.route_table_id
        rules = await self.routing.list_rules()
        if not any(r.table_id == table_id and r.fwmark == dongle.fwmark for r in rules):
            logger.info(
                "Dongle %s : règle fwmark %s (table %d) absente, re-posée",
                dongle.id, dongle.fwmark, table_id,
            )
            await self.routing.add_rule(table_id, dongle.fwmark)
        if bearer is None or not bearer.address:
            # Bearer sans IPv4 (ex. session PPP sans port net) : ne poser
            # aucune route — il n'y a pas de source à épingler.
            logger.debug("Dongle %s : aucun bearer IPv4 courant, route par défaut non posée", dongle.id)
            return
        await self._apply_default_route(dongle, table_id, bearer, interface_name)
        await self._apply_source_rule(dongle, table_id, rules, bearer)

    async def _apply_default_route(
        self, dongle: Dongle, table_id: int, bearer: BearerIpConfig, interface_name: str
    ) -> None:
        """Route par défaut de la table dédiée ; sans passerelle, fallback scope link."""
        desired_via = bearer.gateway or None
        routes = await self.routing.list_routes(table_id)
        existing = next((r for r in routes if r.dest == "default"), None)
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
