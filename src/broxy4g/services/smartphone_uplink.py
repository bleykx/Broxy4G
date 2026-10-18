"""Service d'uplink mobile via partage USB (RNDIS) d'un smartphone.

Le téléphone est résolu par serial (sysfs, jamais de nom codé en dur) ;
NetworkManager fournit l'adresse IPv4 sans jamais créer de seconde route
par défaut globale. ``guard_default_route`` est le filet de sécurité :
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
        if not await self.nm.ensure_profile(interface, profile, PROFILE_METRIC):
            raise SmartphoneUplinkError(f"profil NetworkManager « {profile} » incorrect")
        if not await self.nm.up(profile):
            raise SmartphoneUplinkError(f"mise en ligne du profil « {profile} » impossible")
        lease = await self._wait_lease(profile)
        if lease is None:
            raise SmartphoneUplinkError("adresse IP non attribuée au téléphone (DHCP)")
        await self.guard_default_route(interface)
        return BearerIpConfig(
            address=lease.address,
            prefix=lease.prefix,
            gateway=await self.nm.dhcp_gateway(profile),
            method="dhcp",
        )

    async def _wait_lease(self, profile: str) -> NmIpv4 | None:
        for _ in range(self.dhcp_wait_attempts):
            lease = await self.nm.profile_ipv4(profile)
            if lease is not None:
                return lease
            await asyncio.sleep(self.dhcp_wait_delay)
        return await self.nm.profile_ipv4(profile)

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

        Scooping au profil (fail-closed) : une adresse sur le device qui n'appartient
        pas à notre profil ne fabrique jamais de bearer — l'appelant force alors
        le plancher au lieu de sourcer la route avec une adresse étrangère.
        """
        profile = self.profile_name(serial)
        lease = await self.nm.profile_ipv4(profile)
        if lease is None:
            return None
        return BearerIpConfig(
            address=lease.address,
            prefix=lease.prefix,
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
        """Vrai si le profil du serial porte encore exactement ``address``.

        Scooping au profil (fail-closed) : une adresse d'une autre connexion
        sur l'interface ne compte pas comme vivante.
        """
        lease = await self.nm.profile_ipv4(self.profile_name(serial))
        return lease is not None and lease.address == address
