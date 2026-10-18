"""Adaptateur NetworkManager (nmcli) pour l'uplink RNDIS d'un smartphone.

Facts mesurés sur hôte réel :
- ``con add type ethernet ... ipv4.never-default yes`` + ``con up`` donne une
  adresse IPv4 SANS seconde route par défaut globale.
- La passerelle DHCP se lit dans ``nmcli -f DHCP4 con show <name>``
  (``DHCP4.OPTION[27]:  routers = 192.168.172.245``), pas dans ``IP4.GATEWAY``.
- L'adresse runtime se lit avec ``nmcli -g IP4.ADDRESS device show <if>``
  (``192.168.172.78/24``), le profil ``con show`` n'a pas de section IP4.

Toutes les commandes passent par ``ShellAdapter`` (jamais de ``subprocess``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .shell import ShellAdapter

# Métrique de la route du profil smartphone : élevée, jamais par défaut.
PROFILE_METRIC = 21000

_VERIFY_FIELDS = "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,connection.autoconnect"
_ROUTERS_RE = re.compile(r"\brouters\s*=\s*(\S+)")


@dataclass(frozen=True)
class NmIpv4:
    address: str
    prefix: int


@runtime_checkable
class NetworkManagerAdapter(Protocol):
    async def ensure_profile(self, interface: str, name: str, metric: int = PROFILE_METRIC) -> bool:
        ...

    async def up(self, name: str) -> bool:
        ...

    async def down(self, name: str) -> bool:
        ...

    async def delete_profile(self, name: str) -> bool:
        ...

    async def profile_exists(self, name: str) -> bool:
        ...

    async def device_ipv4(self, interface: str) -> NmIpv4 | None:
        ...

    async def dhcp_gateway(self, name: str) -> str | None:
        ...


class RealNetworkManager:
    def __init__(self, shell: ShellAdapter, nmcli_path: str = "nmcli"):
        self.shell = shell
        self.nmcli = nmcli_path

    async def profile_exists(self, name: str) -> bool:
        result = await self.shell.run([self.nmcli, "con", "show", name])
        return result.returncode == 0 and bool(result.stdout.strip())

    async def ensure_profile(self, interface: str, name: str, metric: int = PROFILE_METRIC) -> bool:
        """Create-or-modify : ``con add`` si absent, ``con mod`` sinon ; vérifier ensuite."""
        settings = [
            "ipv4.method", "auto",
            "ipv4.never-default", "yes",
            "ipv6.method", "disabled",
            "ipv4.route-metric", str(metric),
            "connection.autoconnect", "yes",
        ]
        if await self.profile_exists(name):
            await self.shell.run([self.nmcli, "con", "mod", name, *settings])
        else:
            await self.shell.run(
                [self.nmcli, "con", "add", "type", "ethernet",
                 "ifname", interface, "con-name", name, *settings]
            )
        return await self._verify_profile(name, metric)

    async def _verify_profile(self, name: str, metric: int) -> bool:
        result = await self.shell.run([self.nmcli, "-g", _VERIFY_FIELDS, "con", "show", name])
        if result.returncode != 0:
            return False
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return values == ["auto", "yes", "disabled", str(metric), "yes"]

    async def up(self, name: str) -> bool:
        return (await self.shell.run([self.nmcli, "con", "up", name])).returncode == 0

    async def down(self, name: str) -> bool:
        return (await self.shell.run([self.nmcli, "con", "down", name])).returncode == 0

    async def delete_profile(self, name: str) -> bool:
        return (await self.shell.run([self.nmcli, "con", "delete", name])).returncode == 0

    async def device_ipv4(self, interface: str) -> NmIpv4 | None:
        """Première adresse de ``nmcli -g IP4.ADDRESS device show <if>`` (``addr/prefix``)."""
        result = await self.shell.run([self.nmcli, "-g", "IP4.ADDRESS", "device", "show", interface])
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            value = line.strip()
            if not value:
                continue
            address, _, prefix = value.partition("/")
            if not address:
                continue
            return NmIpv4(address=address, prefix=int(prefix) if prefix else 0)
        return None

    async def dhcp_gateway(self, name: str) -> str | None:
        """Première passerelle de ``nmcli -f DHCP4 con show <name>`` (``routers = <ip>``)."""
        result = await self.shell.run([self.nmcli, "-f", "DHCP4", "con", "show", name])
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            match = _ROUTERS_RE.search(line)
            if match:
                return match.group(1)
        return None


class FakeNetworkManager:
    """Stub en mémoire, sans ShellAdapter (``calls`` journalise les opérations)."""

    def __init__(self):
        self.profiles: dict[str, dict] = {}
        self.devices: dict[str, NmIpv4] = {}
        self.gateways: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.up_result: bool = True

    async def ensure_profile(self, interface: str, name: str, metric: int = PROFILE_METRIC) -> bool:
        self.calls.append(["ensure_profile", interface, name, str(metric)])
        self.profiles[name] = {
            "interface": interface,
            "metric": metric,
            "ipv4.method": "auto",
            "ipv4.never-default": "yes",
            "ipv6.method": "disabled",
            "connection.autoconnect": "yes",
        }
        return True

    async def up(self, name: str) -> bool:
        self.calls.append(["up", name])
        return self.up_result

    async def down(self, name: str) -> bool:
        self.calls.append(["down", name])
        return name in self.profiles

    async def delete_profile(self, name: str) -> bool:
        self.calls.append(["delete_profile", name])
        self.profiles.pop(name, None)
        return True

    async def profile_exists(self, name: str) -> bool:
        return name in self.profiles

    async def device_ipv4(self, interface: str) -> NmIpv4 | None:
        return self.devices.get(interface)

    async def dhcp_gateway(self, name: str) -> str | None:
        return self.gateways.get(name)
