"""Adaptateur ADB pour dongles Android (tethering RNDIS, mode avion).

Comportement validé sur Samsung SM-A217F (Android 12, non-root) :

- ``svc usb setFunctions`` **rejette** les listes séparées par virgule
  (rc=137) : émettre toujours une seule fonction.
- ``rndis`` seul suffit : le framework ajoute ``adb`` lui-même
  (``sys.usb.state`` → ``rndis,adb``).
- Le basculement coupe ``adbd`` ~5 s : ``rc=255`` est transitoire,
  jamais un verdict d'échec.
- ``cmd connectivity airplane-mode`` n'accepte que ``enable``/``disable``.
- ``svc usb setFunctions rndis`` a pu ne pas s'appliquer au 1er essai :
  vérifier (``getprop sys.usb.state``) et réessayer (borné).

Toutes les commandes passent par ``ShellAdapter`` (jamais de ``subprocess``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .shell import ShellAdapter, ShellResult


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    model: str | None = None
    product: str | None = None


@runtime_checkable
class AdbAdapter(Protocol):
    async def list_devices(self) -> list[AdbDevice]:
        ...

    async def get_prop(self, serial: str, prop: str) -> str | None:
        ...

    async def set_tether_dun_required(self, serial: str) -> bool:
        ...

    async def enable_usb_tethering(self, serial: str, attempts: int = 3, retry_delay: float = 3.0) -> bool:
        ...

    async def disable_usb_tethering(self, serial: str) -> bool:
        ...

    async def airplane_cycle(self, serial: str, delay: float = 5.0) -> bool:
        ...

    async def wait_for_device(self, serial: str, timeout: float = 30.0, poll: float = 2.0) -> bool:
        ...

    async def restart_server(self) -> bool:
        ...


def _parse_devices(output: str) -> list[AdbDevice]:
    """Parse la sortie ``adb devices -l`` (en-têtes et lignes daemon ignorées)."""
    devices: list[AdbDevice] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.lower().startswith("list of devices"):
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        model = product = None
        for token in tokens[2:]:
            if token.startswith("model:"):
                model = token.split(":", 1)[1]
            elif token.startswith("product:"):
                product = token.split(":", 1)[1]
        devices.append(AdbDevice(serial=tokens[0], state=tokens[1], model=model, product=product))
    return devices


class RealAdb:
    def __init__(self, shell: ShellAdapter, adb_path: str = "adb"):
        self.shell = shell
        self.adb_path = adb_path

    async def _shell(self, serial: str, *command: str) -> ShellResult:
        return await self.shell.run([self.adb_path, "-s", serial, "shell", *command])

    async def list_devices(self) -> list[AdbDevice]:
        result = await self.shell.run([self.adb_path, "devices", "-l"])
        if result.returncode != 0:
            return []
        return _parse_devices(result.stdout)

    async def get_prop(self, serial: str, prop: str) -> str | None:
        result = await self._shell(serial, "getprop", prop)
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    async def set_tether_dun_required(self, serial: str) -> bool:
        result = await self._shell(serial, "settings", "put", "global", "tether_dun_required", "0")
        return result.returncode == 0

    async def enable_usb_tethering(self, serial: str, attempts: int = 3, retry_delay: float = 3.0) -> bool:
        """Active ``rndis`` (seule fonction) et vérifie ``sys.usb.state`` (borné)."""
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(retry_delay)
            await self._shell(serial, "svc", "usb", "setFunctions", "rndis")
            state = await self.get_prop(serial, "sys.usb.state")
            if state is not None and "rndis" in state:
                return True
        return False

    async def disable_usb_tethering(self, serial: str) -> bool:
        await self._shell(serial, "svc", "usb", "setFunctions", "mtp")
        state = await self.get_prop(serial, "sys.usb.state")
        return state is not None and "mtp" in state

    async def _airplane_step(self, serial: str, mode: str, delay: float) -> bool:
        """Une étape ``cmd connectivity airplane-mode`` ; un rc=255 est réessayé une fois."""
        for _ in range(2):
            result = await self._shell(serial, "cmd", "connectivity", "airplane-mode", mode)
            if result.returncode == 0:
                return True
            if result.returncode != 255:
                return False
            await asyncio.sleep(delay)
        return False

    async def airplane_cycle(self, serial: str, delay: float = 5.0) -> bool:
        """Double cycle avion : enable, disable, enable, disable (``delay`` entre chaque)."""
        for index, mode in enumerate(("enable", "disable", "enable", "disable")):
            if not await self._airplane_step(serial, mode, delay):
                return False
            if index < 3:
                await asyncio.sleep(delay)
        return True

    async def wait_for_device(self, serial: str, timeout: float = 30.0, poll: float = 2.0) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for device in await self.list_devices():
                if device.serial == serial and device.state == "device":
                    return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(poll)

    async def restart_server(self) -> bool:
        """Relance bornée d'un serveur adb stale : kill/start + ``reconnect offline``."""
        try:
            await self.shell.run([self.adb_path, "kill-server"], timeout=10.0)
            result = await self.shell.run([self.adb_path, "start-server"], timeout=15.0)
            if result.returncode != 0:
                return False
            await self.shell.run([self.adb_path, "reconnect", "offline"], timeout=10.0)
            return True
        except Exception:
            return False


class FakeAdb:
    """Stub en mémoire, utilisable sans ShellAdapter (``calls`` journalise les commandes)."""

    def __init__(self):
        self.devices: list[AdbDevice] = []
        self.props: dict[str, str] = {}
        self.usb_state: str = "mtp,adb"
        self.calls: list[list[str]] = []
        self.fail_enable_times: int = 0
        self.restart_count: int = 0
        self.fail_restart_times: int = 0

    async def list_devices(self) -> list[AdbDevice]:
        return list(self.devices)

    async def get_prop(self, serial: str, prop: str) -> str | None:
        self.calls.append(["getprop", prop])
        return self.props.get(prop)

    async def set_tether_dun_required(self, serial: str) -> bool:
        self.calls.append(["settings", "put", "global", "tether_dun_required", "0"])
        return True

    async def enable_usb_tethering(self, serial: str, attempts: int = 3, retry_delay: float = 3.0) -> bool:
        for _ in range(attempts):
            self.calls.append(["svc", "usb", "setFunctions", "rndis"])
            if self.fail_enable_times > 0:
                self.fail_enable_times -= 1
                continue
            self.usb_state = "rndis,adb"
            return True
        return False

    async def disable_usb_tethering(self, serial: str) -> bool:
        self.calls.append(["svc", "usb", "setFunctions", "mtp"])
        self.usb_state = "mtp,adb"
        return True

    async def airplane_cycle(self, serial: str, delay: float = 5.0) -> bool:
        for mode in ("enable", "disable", "enable", "disable"):
            self.calls.append(["cmd", "connectivity", "airplane-mode", mode])
        self.usb_state = "mtp,adb"
        return True

    async def wait_for_device(self, serial: str, timeout: float = 30.0, poll: float = 2.0) -> bool:
        return any(device.serial == serial and device.state == "device" for device in self.devices)

    async def restart_server(self) -> bool:
        self.restart_count += 1
        if self.fail_restart_times > 0:
            self.fail_restart_times -= 1
            return False
        return True
