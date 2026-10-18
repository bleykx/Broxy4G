"""Service d'uplink mobile via partage USB (RNDIS) d'un smartphone.

Le téléphone est résolu par serial (sysfs, jamais de nom codé en dur) ;
NetworkManager fournit l'adresse IPv4 sans jamais créer de seconde route
par défaut globale. Le profil NM est lié par **MAC** (identité matérielle)
et les connexions NM **étrangères** de l'interface (ex. ``Wired connection N``)
sont **supprimées** dès ``bring_up`` (écriture partielle impossible sous le
décalage client/démon ; le profil lié par MAC empêche la recréation).
``guard_default_route`` est le filet de sécurité :
si une route par défaut de la table main passe par l'interface du
téléphone, elle est journalisée et supprimée (règle projet : l'hôte ne
doit JAMAIS avoir sa route par défaut par un de ces devices).
"""

from __future__ import annotations

import asyncio
import logging
import re

from ..adapters.adb import AdbAdapter
from ..adapters.modem_manager import BearerIpConfig
from ..adapters.net_interface import InterfaceAdapter
from ..adapters.network_manager import (
    PROFILE_METRIC,
    NetworkManagerAdapter,
    NmIpv4,
)
from ..adapters.routing import RoutingAdapter
from ..adapters.usb_sysfs_net import USB_DEVICES_ROOT, find_net_interfaces_for_usb_serial

logger = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class SmartphoneUplinkError(Exception):
    """Erreur métier du uplink smartphone (message lisible, sans jargon technique)."""


class SmartphoneUplinkService:
    def __init__(
        self,
        adb: AdbAdapter,
        nm: NetworkManagerAdapter,
        iface: InterfaceAdapter,
        routing: RoutingAdapter,
        sysfs_root: str = USB_DEVICES_ROOT,
        iface_wait_attempts: int = 20,
        iface_wait_delay: float = 1.0,
        dhcp_wait_attempts: int = 20,
        dhcp_wait_delay: float = 1.0,
    ):
        self.adb = adb
        self.nm = nm
        self.iface = iface
        self.routing = routing
        self.sysfs_root = sysfs_root
        self.iface_wait_attempts = iface_wait_attempts
        self.iface_wait_delay = iface_wait_delay
        self.dhcp_wait_attempts = dhcp_wait_attempts
        self.dhcp_wait_delay = dhcp_wait_delay

    def profile_name(self, serial: str) -> str:
        return f"broxy4g-phone-{_SAFE_NAME_RE.sub('_', serial)}"

    def resolve_interface(self, serial: str) -> str | None:
        """Interface RNDIS du serial via sysfs ; ``up`` d'abord, sinon la première."""
        interfaces = find_net_interfaces_for_usb_serial(serial, self.sysfs_root)
        if not interfaces:
            return None
        for candidate in interfaces:
            if candidate.operstate == "up":
                return candidate.name
        return interfaces[0].name

    async def wait_for_interface(self, serial: str) -> str | None:
        for _ in range(self.iface_wait_attempts):
            name = self.resolve_interface(serial)
            if name is not None:
                return name
            await asyncio.sleep(self.iface_wait_delay)
        return self.resolve_interface(serial)

    async def bring_up(self, serial: str) -> BearerIpConfig:
        profile = self.profile_name(serial)
        await self.adb.set_tether_dun_required(serial)
        if not await self.adb.enable_usb_tethering(serial):
            raise SmartphoneUplinkError(
                "activation du partage de connexion USB impossible sur le téléphone"
            )
        interface = await self.wait_for_interface(serial)
        if interface is None:
            raise SmartphoneUplinkError("interface réseau du téléphone introuvable")
        mac = await self.iface.mac_address(interface)  # None → repli interface-name (averti)
        if not await self.nm.ensure_profile(interface, mac, profile, PROFILE_METRIC):
            raise SmartphoneUplinkError(f"profil NetworkManager « {profile} » incorrect")
        await self.nm.remove_foreign_connections(interface, mac, profile)
        if not await self.nm.up(profile):
            raise SmartphoneUplinkError(f"mise en ligne du profil « {profile} » impossible")
        lease = await self._wait_lease(profile)
        if lease is None:
            raise SmartphoneUplinkError("adresse IP non attribuée au téléphone (DHCP)")
        await self.guard_default_route(interface)
        bearer = await self._bearer_address(profile, interface)
        if bearer is None:
            # Course possible : le bail vient d'être confirmé mais le profil a
            # perdu son adresse entre les deux lectures → échec métier, pas de
            # bearer fabriqué.
            raise SmartphoneUplinkError("adresse IP non attribuée au téléphone (DHCP)")
        return BearerIpConfig(
            address=bearer.address,
            prefix=bearer.prefix,
            gateway=await self.nm.dhcp_gateway(profile),
            method="dhcp",
        )

    async def _wait_lease(self, profile: str) -> NmIpv4 | None:
        """Boucle d'attente du **bail** (gate ``profile_ipv4``) ; distincte de la
        valeur de l'adresse (``_bearer_address``) : l'appelant doit distinguer
        « pas de bail » de « pas d'adresse noyau »."""
        for _ in range(self.dhcp_wait_attempts):
            lease = await self.nm.profile_ipv4(profile)
            if lease is not None:
                return lease
            await asyncio.sleep(self.dhcp_wait_delay)
        return await self.nm.profile_ipv4(profile)

    async def _bearer_address(self, profile: str, interface: str) -> NmIpv4 | None:
        """Adresse IPv4 du bearer : **présence** scopée au profil, **valeur** lue
        dans le noyau.

        La présence vient du profil (fail-closed) : ``nm.profile_ipv4`` non
        ``None`` ⇔ notre profil porte une adresse (notre connexion est active) ;
        une adresse étrangère sur l'interface ne compte jamais. La valeur vient
        du noyau (``primary_ipv4``) : l'ordre de la sortie ``nmcli`` n'est pas
        fiable (deux adresses sur une ligne, ordre observé variable), or c'est
        l'adresse primaire du noyau que le noyau utilise réellement comme source.
        Repli documenté : si la lecture noyau échoue (``None``), on repart de
        l'adresse du bail NM — jamais d'exception, jamais d'adresse inventée.
        """
        lease = await self.nm.profile_ipv4(profile)
        if lease is None:
            return None
        kernel = await self.iface.primary_ipv4(interface)
        if kernel is None:
            return lease
        return NmIpv4(address=kernel[0], prefix=kernel[1])

    async def bring_down(self, serial: str) -> None:
        """Stop profil puis désactive le partage USB ; jamais d'exception transitoire."""
        profile = self.profile_name(serial)
        try:
            if not await self.nm.down(profile):
                logger.warning("uplink smartphone : arrêt du profil « %s » en échec", profile)
        except Exception:
            logger.warning("uplink smartphone : erreur en arrêtant « %s »", profile, exc_info=True)
        try:
            if not await self.adb.disable_usb_tethering(serial):
                logger.warning("uplink smartphone : partage USB du téléphone non désactivé")
        except Exception:
            logger.warning("uplink smartphone : erreur en désactivant le partage USB", exc_info=True)

    async def current_bearer(self, serial: str) -> BearerIpConfig | None:
        """Bearer courant (lecture seule, sans activer le partage) ; ``None`` si indispo.

        Présence scopée au profil (fail-closed) : une adresse sur le device qui
        n'appartient pas à notre profil ne fabrique jamais de bearer — l'appelant
        force alors le plancher au lieu de sourcer la route avec une adresse
        étrangère. La valeur vient du noyau (``_bearer_address``), jamais de
        ``nmcli`` : on re-résout l'interface (``None`` → ``None``, plus d'adresse
        fabriquée sans interface).
        """
        interface = self.resolve_interface(serial)
        if interface is None:
            return None
        profile = self.profile_name(serial)
        bearer = await self._bearer_address(profile, interface)
        if bearer is None:
            return None
        return BearerIpConfig(
            address=bearer.address,
            prefix=bearer.prefix,
            gateway=await self.nm.dhcp_gateway(profile),
            method="dhcp",
        )

    async def guard_default_route(self, interface: str) -> None:
        """L'hôte ne doit JAMAIS avoir sa route par défaut via ce device : suppression."""
        offenders = [r for r in await self.routing.list_default_routes() if r.dev == interface]
        if not offenders:
            return
        logger.warning(
            "ACTION ADMIN : route par défaut de la table main via %s — suppression", interface
        )
        await self.routing.remove_default_route(interface)
        still = [r for r in await self.routing.list_default_routes() if r.dev == interface]
        if still:
            raise SmartphoneUplinkError(f"route par défaut de l'hôte détournée via {interface}")

    async def is_live(self, serial: str, address: str) -> bool:
        """Vrai si le bearer porté par notre profil est encore exactement ``address``.

        Présence scopée au profil (fail-closed) : une adresse d'une autre connexion
        sur l'interface ne compte pas comme vivante. La valeur comparée est celle
        du noyau (``_bearer_address``) — repli sur le bail si la lecture noyau
        échoue. Jamais d'exception.
        """
        interface = self.resolve_interface(serial)
        if interface is None:
            return False
        bearer = await self._bearer_address(self.profile_name(serial), interface)
        return bearer is not None and bearer.address == address
