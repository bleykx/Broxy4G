"""Probes de connectivité épinglés à l'IP source du bearer d'un dongle.

Épingler à l'IP source (et non au nom d'interface) garantit que le trafic
sort bien par l'uplink du dongle et non par la table de routage principale
(``curl --interface <IP>`` / ``ping -I <IP>`` acceptent une adresse source).
"""

from broxy4g.adapters.connectivity import ConnectivityAdapter, PingResult
from broxy4g.adapters.net_interface import InterfaceAdapter


class UplinkProbe:
    """Mesure ping / IP publique en forçant la source IPv4 du bearer."""

    def __init__(self, iface: InterfaceAdapter, connectivity: ConnectivityAdapter):
        self.iface = iface
        self.connectivity = connectivity

    async def source_ip(self, interface_name: str | None) -> str | None:
        """IP source du bearer sur l'interface ; None si pas d'interface."""
        if not interface_name:
            return None
        return await self.iface.get_ip(interface_name)

    async def public_ip(self, interface_name: str | None) -> str | None:
        source = await self.source_ip(interface_name)
        if not source:
            return None
        return await self.connectivity.get_public_ip(source)

    async def ping(self, interface_name: str | None, host: str = "8.8.8.8") -> PingResult | None:
        source = await self.source_ip(interface_name)
        if not source:
            return None
        return await self.connectivity.ping(host, interface=source)
