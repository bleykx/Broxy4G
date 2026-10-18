"""Gestion des interfaces réseau de l'hôte via iproute2 (ip).

NetworkManager n'est pas fiable pour les interfaces WWAN sur certaines
distributions ; cette couche encapsule les appels ``ip`` testables.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class InterfaceAdapter(Protocol):
    async def set_up(self, name: str) -> None:
        ...

    async def set_down(self, name: str) -> None:
        ...

    async def assign_address(self, name: str, address: str, prefix: int) -> None:
        ...

    async def flush_ipv4(self, name: str) -> None:
        ...

    async def get_ip(self, name: str) -> str | None:
        ...

    async def primary_ipv4(self, name: str) -> tuple[str, int] | None:
        ...


class RealInterface:
    def __init__(self, shell):
        self.shell = shell

    async def _run_checked(self, action: str, command: list[str]) -> None:
        result = await self.shell.run(command)
        if result.returncode != 0:
            raise RuntimeError(
                f"{action} failed (rc={result.returncode}): {result.stderr or result.stdout}"
            )

    async def set_up(self, name: str) -> None:
        await self._run_checked("ip link up", ["ip", "link", "set", "dev", name, "up"])

    async def set_down(self, name: str) -> None:
        await self._run_checked("ip link down", ["ip", "link", "set", "dev", name, "down"])

    async def assign_address(self, name: str, address: str, prefix: int) -> None:
        await self._run_checked(
            "ip addr replace", ["ip", "addr", "replace", f"{address}/{prefix}", "dev", name]
        )

    async def flush_ipv4(self, name: str) -> None:
        await self._run_checked("ip addr flush", ["ip", "-4", "addr", "flush", "dev", name])

    async def get_ip(self, name: str) -> str | None:
        result = await self.shell.run(["ip", "-o", "-4", "addr", "show", "dev", name])
        if result.returncode != 0:
            return None
        # Ligne: "4: wwan0    inet 10.0.0.2/30 brd ... scope global wwan0"
        for line in result.stdout.splitlines():
            parts = line.split()
            if "inet" in parts:
                value = parts[parts.index("inet") + 1]
                return value.split("/", 1)[0]
        return None

    async def primary_ipv4(self, name: str) -> tuple[str, int] | None:
        """Adresse primaire IPv4 telle que lue par le **noyau** (``scope global``).

        L'ordre de la sortie ``ip`` est l'ordre d'insertion : l'adresse primaire
        d'abord, les ``secondary`` ensuite. C'est l'adresse que le noyau retient
        comme source pour une route sans ``prefsrc``. L'ordre de la sortie
        ``nmcli`` n'est PAS fiable : cas mesuré sur hôte réel (Galaxy A21S,
        RNDIS) — le profil imprimait ``192.168.172.79/24 | 192.168.172.78/24``
        (l'ordre variant entre deux cycles) alors que le noyau sourçait
        ``192.168.172.78`` (primaire) et non ``.79`` (secondary). Retient la
        première entrée ``inet`` dont les drapeaux ne contiennent pas
        ``secondary``. Jamais d'exception : rc ≠ 0, sortie vide, ligne illisible
        ou préfixe corrompu → ``None`` (ou l'entrée suivante).
        """
        result = await self.shell.run(
            ["ip", "-o", "-4", "addr", "show", "dev", name, "scope", "global"]
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            parts = line.split()
            if "inet" not in parts or "secondary" in parts:
                continue
            if "scope" in parts and parts[parts.index("scope") + 1] != "global":
                continue
            inet_idx = parts.index("inet")
            if inet_idx + 1 >= len(parts):
                continue
            address, _, prefix = parts[inet_idx + 1].partition("/")
            if prefix and not prefix.isdigit():
                continue
            return address, int(prefix) if prefix else 0
        return None


class FakeInterface:
    def __init__(self):
        # name -> liste ordonnée de (adresse, préfixe, secondary), dans l'ordre
        # d'insertion du noyau (primaire d'abord, secondaires ensuite).
        self._addresses: dict[str, list[tuple[str, int, bool]]] = {}
        self.up: set[str] = set()

    def set_address(self, name: str, address: str | None) -> None:
        if address is None:
            self._addresses.pop(name, None)
        else:
            self._addresses[name] = [(address, 0, False)]

    def set_addresses(self, name: str, entries: list[tuple[str, int, bool]]) -> None:
        """Porte plusieurs adresses dans l'ordre ( ``(adresse, préfixe, secondary)`` )."""
        self._addresses[name] = list(entries)

    async def set_up(self, name: str) -> None:
        self.up.add(name)

    async def set_down(self, name: str) -> None:
        self.up.discard(name)

    async def assign_address(self, name: str, address: str, prefix: int) -> None:
        self.up.add(name)
        self._addresses[name] = [(address, prefix, False)]

    async def flush_ipv4(self, name: str) -> None:
        self._addresses.pop(name, None)

    async def get_ip(self, name: str) -> str | None:
        entries = self._addresses.get(name)
        return entries[0][0] if entries else None

    async def primary_ipv4(self, name: str) -> tuple[str, int] | None:
        for address, prefix, secondary in self._addresses.get(name, []):
            if not secondary:
                return address, prefix
        return None
