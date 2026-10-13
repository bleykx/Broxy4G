"""Découverte des uplinks locaux de l'hôte (ethernet / wifi) via iproute2 et sysfs.

Ne concerne QUE les interfaces de l'hôte : les interfaces WWAN (dongles/modems,
reconnues par ``DEVTYPE=wwan`` dans leur ``uevent``) sont exclues.
Le chemin ``/sys/class/net`` est injectable pour rendre la classification testable.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .shell import ShellAdapter

_EXCLUDED_PREFIXES = ("docker", "br-", "veth", "virbr")
_VIA_RE = re.compile(r"\bvia\s+(\S+)")
_DNS_IP_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_NAMESERVER_RE = re.compile(r"^\s*nameserver\s+(\S+)")
_LINK_UP = frozenset({"up"})
_LINK_DOWN = frozenset({"down", "lowerlayerdown", "notpresent"})


@dataclass(frozen=True)
class LocalInterface:
    name: str
    ip: str | None
    kind: str  # "ethernet" | "wifi" | "other"


@runtime_checkable
class LocalUplinkAdapter(Protocol):
    async def list_interfaces(self) -> list[LocalInterface]:
        ...

    async def default_gateway(self, name: str) -> str | None:
        ...

    async def link_state(self, name: str) -> str:
        """État du lien : ``"up"`` | ``"down"`` | ``"unknown"``."""
        ...

    async def dns(self, name: str) -> list[str] | None:
        """Serveurs DNS effectifs de l'interface, ou ``None`` si inconnus."""
        ...


def _parse_addr_lines(stdout: str) -> dict[str, str]:
    """Extrait la première adresse IPv4 par interface (``ip -o -4 addr show``)."""
    addresses: dict[str, str] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        name = parts[1].split(":", 1)[0]
        addresses.setdefault(name, parts[parts.index("inet") + 1].split("/", 1)[0])
    return addresses


class RealLocalUplink:
    def __init__(
        self,
        shell: ShellAdapter,
        sys_class_net: Path = Path("/sys/class/net"),
        resolv_conf: Path = Path("/etc/resolv.conf"),
    ):
        self.shell = shell
        self.sys_class_net = sys_class_net
        self.resolv_conf = resolv_conf

    @staticmethod
    def _is_excluded(name: str) -> bool:
        return name == "lo" or name.startswith(_EXCLUDED_PREFIXES)

    def _classify(self, name: str) -> str:
        if (self.sys_class_net / name / "wireless").exists():
            return "wifi"
        if (self.sys_class_net / name / "device").exists():
            return "ethernet"
        return "other"

    def _is_wwan(self, name: str) -> bool:
        uevent = self.sys_class_net / name / "uevent"
        try:
            return "DEVTYPE=wwan" in uevent.read_text().splitlines()
        except OSError:
            return False

    async def list_interfaces(self) -> list[LocalInterface]:
        result = await self.shell.run(["ip", "-o", "-4", "addr", "show"])
        if result.returncode != 0:
            return []
        interfaces = [
            LocalInterface(name, ip, self._classify(name))
            for name, ip in _parse_addr_lines(result.stdout).items()
            if not self._is_excluded(name) and not self._is_wwan(name)
        ]
        return sorted(interfaces, key=lambda i: i.name)

    async def default_gateway(self, name: str) -> str | None:
        result = await self.shell.run(["ip", "-o", "-4", "route", "show", "default", "dev", name])
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            match = _VIA_RE.search(line)
            if match:
                return match.group(1)
        return None

    async def link_state(self, name: str) -> str:
        operstate = self._read_sys(name, "operstate")
        if operstate is not None:
            if operstate in _LINK_UP:
                return "up"
            if operstate in _LINK_DOWN:
                return "down"
        carrier = self._read_sys(name, "carrier")
        if carrier == "1":
            return "up"
        if carrier == "0":
            return "down"
        return "unknown"

    def _read_sys(self, name: str, attr: str) -> str | None:
        """Lit un attribut sysfs ; ``None`` si lisible impossible ou absent."""
        try:
            return (self.sys_class_net / name / attr).read_text().strip()
        except OSError:
            return None

    async def dns(self, name: str) -> list[str] | None:
        result = await self.shell.run(["resolvectl", "dns", name])
        if result.returncode == 0:
            servers = self._parse_resolvectl(result.stdout, name)
            if servers:
                return servers
        return self._resolv_conf_nameservers()

    @staticmethod
    def _parse_resolvectl(stdout: str, name: str) -> list[str]:
        for line in stdout.splitlines():
            if line.startswith(f"{name}:"):
                return _DNS_IP_RE.findall(line.split(":", 1)[1])
        return []

    def _resolv_conf_nameservers(self) -> list[str] | None:
        try:
            lines = self.resolv_conf.read_text().splitlines()
        except OSError:
            return None
        servers = [m.group(1) for m in map(_NAMESERVER_RE.match, lines) if m]
        return servers or None


class FakeLocalUplink:
    def __init__(self):
        self._interfaces: list[LocalInterface] = []
        self._gateways: dict[str, str] = {}
        self._link_states: dict[str, str] = {}
        self._dns: dict[str, list[str]] = {}

    def set_interfaces(self, interfaces: list[LocalInterface]) -> None:
        self._interfaces = list(interfaces)

    def set_gateway(self, name: str, gateway: str | None) -> None:
        if gateway is None:
            self._gateways.pop(name, None)
        else:
            self._gateways[name] = gateway

    def set_link_state(self, name: str, state: str | None) -> None:
        if state is None:
            self._link_states.pop(name, None)
        else:
            self._link_states[name] = state

    def set_dns(self, name: str, servers: list[str] | None) -> None:
        if servers is None:
            self._dns.pop(name, None)
        else:
            self._dns[name] = list(servers)

    async def list_interfaces(self) -> list[LocalInterface]:
        return list(self._interfaces)

    async def default_gateway(self, name: str) -> str | None:
        return self._gateways.get(name)

    async def link_state(self, name: str) -> str:
        return self._link_states.get(name, "unknown")

    async def dns(self, name: str) -> list[str] | None:
        return self._dns.get(name)
