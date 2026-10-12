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


class FakeInterface:
    def __init__(self):
        self._addresses: dict[str, str] = {}
        self.up: set[str] = set()

    def set_address(self, name: str, address: str | None) -> None:
        if address is None:
            self._addresses.pop(name, None)
        else:
            self._addresses[name] = address

    async def set_up(self, name: str) -> None:
        self.up.add(name)

    async def set_down(self, name: str) -> None:
        self.up.discard(name)

    async def assign_address(self, name: str, address: str, prefix: int) -> None:
        self.up.add(name)
        self._addresses[name] = address

    async def flush_ipv4(self, name: str) -> None:
        self._addresses.pop(name, None)

    async def get_ip(self, name: str) -> str | None:
        return self._addresses.get(name)
