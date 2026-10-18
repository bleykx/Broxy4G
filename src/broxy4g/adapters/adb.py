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

Protocole, modèle et stub en mémoire ici ; l'implémentation réelle
(``RealAdb``, commandes via ``ShellAdapter``) vit dans ``adb_real.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


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
