"""Cycle de vie de connexion d'un dongle téléphone (transport ADB, partage USB).

Complément de ``ConnectionService`` pour les dongles ADB : l'uplink est le
partage USB (RNDIS) du téléphone, pas un modem ModemManager. Le service
détient la transition d'état CONNECTING → ONLINE / ERROR ; l'état RESETTING
pendant un reset reste la propriété du ``ResetService`` externe (ce service
ne fait que sceller, couper la radio, puis reconnecter).
"""

import asyncio
import logging

from broxy4g.adapters.net_interface import InterfaceAdapter
from broxy4g.adapters.routing import RoutingAdapter
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.dongle_slot import ensure_routing_slot
from broxy4g.services.network_isolation import NetworkIsolationService
from broxy4g.services.routing_allocator import RoutingAllocator
from broxy4g.services.smartphone_uplink import SmartphoneUplinkError, SmartphoneUplinkService
from broxy4g.services.uplink_routes import UplinkRouteService

__all__ = ["SmartphoneConnectionService"]

logger = logging.getLogger(__name__)


class SmartphoneConnectionService:
    """Connecte / déconnecte / reset un dongle téléphone via partage USB."""

    def __init__(
        self,
        dongle_repo: DongleRepository,
        uplink: SmartphoneUplinkService,
        iface: InterfaceAdapter,
        routing: RoutingAdapter,
        allocator: RoutingAllocator,
        uplink_routes: UplinkRouteService,
        isolation: NetworkIsolationService | None = None,
        ip_wait_attempts: int = 15,
        ip_wait_delay: float = 2.0,
    ):
        self.dongle_repo = dongle_repo
        self.uplink = uplink
        self.iface = iface
        self.routing = routing
        self.allocator = allocator
        self.uplink_routes = uplink_routes
        self.isolation = isolation
        self.ip_wait_attempts = ip_wait_attempts
        self.ip_wait_delay = ip_wait_delay

    def _serial(self, dongle: Dongle) -> str:
        """Serial ADB du téléphone : ``adb_serial`` sinon la clé d'identité."""
        serial = dongle.identity.adb_serial or dongle.identity.primary_key
        if not serial:
            raise SmartphoneUplinkError("identité ADB absente")
        return serial

    async def _apply_isolation(self) -> None:
        if self.isolation is not None:
            await self.isolation.apply()

    async def _seal(self, dongle: Dongle) -> None:
        """Scelle l'échec : plancher (si slot) + isolation avant toute coupure."""
        if dongle.route_table_id:
            await self.routing.add_barrier_route(dongle.route_table_id)
        await self._apply_isolation()

    async def connect(self, dongle: Dongle) -> Dongle:
        """Établit le partage USB du téléphone et route le dongle (CONNECTING → ONLINE)."""
        dongle.change_state(DongleState.CONNECTING)
        try:
            await ensure_routing_slot(
                dongle, allocator=self.allocator, routing=self.routing, repo=self.dongle_repo
            )
            serial = self._serial(dongle)
            bearer = await self.uplink.bring_up(serial)
            iface_name = self.uplink.resolve_interface(serial)
            if iface_name is None:
                raise SmartphoneUplinkError("interface réseau du téléphone introuvable")
            dongle.interface_name = iface_name
            await self.uplink_routes.apply_bearer(dongle, bearer, iface_name)
            # Boucle d'attente bornée : l'adresse du téléphone doit être confirmée
            # sur l'interface (pas seulement le profil activé).
            for _ in range(self.ip_wait_attempts):
                if await self.uplink.is_live(serial, bearer.address):
                    break
                await asyncio.sleep(self.ip_wait_delay)
            else:
                raise SmartphoneUplinkError("adresse IP du téléphone non confirmée")
            dongle.clear_error()
            dongle.change_state(DongleState.ONLINE)
            await self.dongle_repo.save(dongle)
            await self._apply_isolation()
            return dongle
        except Exception as exc:
            logger.exception("Connexion smartphone %s échouée", dongle.id)
            await self._seal(dongle)
            dongle.fail(f"échec connexion smartphone: {type(exc).__name__}: {exc}")
            await self.dongle_repo.save(dongle)
            return dongle

    async def disconnect(self, dongle: Dongle) -> Dongle:
        """Coupe le partage USB : plancher FORCÉ AVANT toute coupure, puis profil arrêté."""
        await self.uplink_routes.apply_barrier(dongle, force=True)
        await self.uplink.bring_down(self._serial(dongle))
        dongle.change_state(DongleState.OFFLINE)
        await self.dongle_repo.save(dongle)
        await self._apply_isolation()
        return dongle

    async def reset(self, dongle: Dongle) -> Dongle:
        """Reset ADB : libère le bail, cycle radio (mode avion), puis reconnexion.

        Ne change pas l'état du dongle lui-même : l'état RESETTING est tenu par
        le ``ResetService`` qui orchestre ce reset ; seul ``connect`` repasse
        le dongle ONLINE.
        """
        await self._seal(dongle)
        serial = self._serial(dongle)
        profile = self.uplink.profile_name(serial)
        try:
            if not await self.uplink.nm.down(profile):
                logger.warning(
                    "reset smartphone %s : arrêt du profil « %s » en échec", dongle.id, profile
                )
        except Exception:
            logger.warning(
                "reset smartphone %s : erreur en arrêtant le profil « %s »",
                dongle.id, profile, exc_info=True,
            )
        logger.info("ACTION ADMIN : reset smartphone dongle %s — cycle radio (mode avion)", dongle.id)
        if not await self.uplink.adb.airplane_cycle(serial):
            logger.warning("reset smartphone %s : cycle radio en échec, reconnexion tentée", dongle.id)
        if not await self.uplink.adb.wait_for_device(serial):
            logger.warning("reset smartphone %s : téléphone pas encore visible, reconnexion tentée", dongle.id)
        return await self.connect(dongle)

    async def bearer_is_live(self, dongle: Dongle) -> bool:
        """Le partage USB est-il encore exploitable ?

        Vrai uniquement si l'interface résolue via sysfs porte une IPv4 ;
        jamais d'exception (échec sûr : on reconnecte plutôt que de sous-estimer).
        """
        try:
            interface = self.uplink.resolve_interface(self._serial(dongle))
            if interface is None:
                return False
            return bool(await self.iface.get_ip(interface))
        except Exception:
            logger.warning("bearer_is_live smartphone %s indécidable", dongle.id, exc_info=True)
            return False
