"""(Re)pose idempotente des routes d'uplink d'un dongle à partir de son bearer.

Résout le bearer (modem par identité, ou téléphone ADB via RNDIS) et décide de
la politique de route (route réelle vs plancher), puis délègue l'écriture des
artefacts ip/fwmark à ``UplinkRouteWriter``. Réutilisée par
``ConnectionService.connect`` et par la reconciliation, qui détecte ainsi une
route écrasée sur un dongle déjà actif (ONLINE) sans reconnexion du modem.
Aucun changement d'état du dongle ici.

Une table de routage dédiée ne doit JAMAIS rester sans route ``default`` :
sinon le noyau retombe sur la table ``main`` (route par défaut de l'hôte) et
le proxy du dongle fuit par eth. Quand aucune route réelle ne peut être posée
(interface inconnue, bearer sans IPv4, état non actif), le plancher (route
``default unreachable``) est posé à la place.
"""

import logging
from typing import TYPE_CHECKING

from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo, ModemManagerAdapter
from broxy4g.adapters.routing import RoutingAdapter
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.transport import Transport
from broxy4g.services.network_isolation import ACTIVE_INTERFACE_STATES
from broxy4g.services.uplink_route_writer import UplinkRouteWriter

if TYPE_CHECKING:
    from broxy4g.services.smartphone_uplink import SmartphoneUplinkService

logger = logging.getLogger(__name__)


class UplinkRouteService:
    """Pose et répare les routes d'uplink d'un dongle, sans jamais reconnecter le modem."""

    def __init__(
        self,
        modem: ModemManagerAdapter,
        routing: RoutingAdapter,
        smartphone: "SmartphoneUplinkService | None" = None,
    ):
        self.modem = modem
        self.routing = routing
        self.smartphone = smartphone
        self._writer = UplinkRouteWriter(routing)

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
        """(Re)pose les routes d'uplink (chemin reconciliation).

        Dongle dans un état actif (ONLINE/DEGRADED/CONNECTING) : restauration
        de la route réelle depuis le bearer. État non actif : pose du
        plancher FORCÉE (l'ancienne route réelle n'a plus de source valide) —
        une table dédiée inactif ne doit jamais laisser le trafic retomber
        sur la table ``main``. Les retours anticipés (interface inconnue,
        modem introuvable) posent un plancher NON forcé : ils ne dégradent
        jamais une route ``default`` déjà présente.
        """
        if dongle.transport is Transport.ADB:
            return await self._apply_adb(dongle)
        if dongle.state not in ACTIVE_INTERFACE_STATES:
            await self.apply_barrier(dongle, force=True)
            return
        if not dongle.interface_name:
            logger.debug(
                "Dongle %s sans interface connue, routes d'uplink non vérifiées, plancher non forcé",
                dongle.id,
            )
            await self.apply_barrier(dongle)
            return
        modem = await self.resolve_modem(dongle)
        if modem is None:
            logger.warning(
                "Dongle %s : modem introuvable, routes d'uplink non vérifiées, plancher non forcé",
                dongle.id,
            )
            await self.apply_barrier(dongle)
            return
        bearer = await self.modem.get_bearer_ip(modem.path)
        await self.apply_bearer(dongle, bearer, dongle.interface_name)

    async def _apply_adb(self, dongle: Dongle) -> None:
        """Chemin téléphone ADB : bearer du partage USB (RNDIS), jamais de modem.

        Même règle qu'en modem : état non actif → plancher FORCÉ ; interface du
        téléphone introuvable → plancher NON forcé (une route réelle pré-existante
        n'est jamais dégradée). Bearer ``None`` (pas de bail DHCP) : ``apply_bearer``
        force le plancher, son adresse étant la source de la route.

        Filet de sécurité : une route par défaut de la table ``main`` via
        l'interface du téléphone a survécu plusieurs minutes après un
        ``bring_up`` en recette réelle (``guard_default_route`` n'y est appelé
        qu'une fois) — la réparation périodique est donc aussi appelée à
        l'éliminer.
        """
        if dongle.state not in ACTIVE_INTERFACE_STATES:
            await self.apply_barrier(dongle, force=True)
            return
        if self.smartphone is None:
            logger.warning(
                "Dongle %s : service smartphone non configuré, plancher non forcé", dongle.id
            )
            await self.apply_barrier(dongle)
            return
        serial = dongle.identity.adb_serial or dongle.identity.primary_key
        interface = self.smartphone.resolve_interface(serial) or dongle.interface_name
        if not interface:
            logger.warning(
                "Dongle %s : interface du téléphone introuvable, plancher non forcé", dongle.id
            )
            await self.apply_barrier(dongle)
            return
        bearer = await self.smartphone.current_bearer(serial)
        await self.apply_bearer(dongle, bearer, interface)
        await self.smartphone.guard_default_route(interface)

    async def apply_bearer(
        self, dongle: Dongle, bearer: BearerIpConfig | None, interface_name: str | None
    ) -> None:
        """(Re)pose règle fwmark + route par défaut + règle source, de façon idempotente.

        Délégation à ``UplinkRouteWriter`` (voir sa docstring) : l'appelant
        fournit le bearer et l'interface déjà résolus.
        """
        await self._writer.apply_bearer(dongle, bearer, interface_name)

    async def apply_barrier(self, dongle: Dongle, *, force: bool = False) -> None:
        """Assure une route ``default`` dans la table dédiée (plancher).

        Délégation à ``UplinkRouteWriter`` (voir sa docstring) : rien n'est
        écrit si une route ``default`` est déjà présente, sauf ``force=True``.
        """
        await self._writer.apply_barrier(dongle, force=force)
