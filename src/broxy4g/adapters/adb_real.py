"""Implémentation réelle de l'adaptateur ADB (``RealAdb``).

Toutes les commandes passent par ``ShellAdapter`` (jamais de ``subprocess``) ;
le chemin du binaire provient de ``BROXY4G_ADB_PATH`` (défaut ``adb``).

Si ``adb devices -l`` échoue (binaire absent, non autorisé, serveur
indisponible), un WARNING actionnable est émis **une seule fois par
instance** — la découverte tourne toutes les ~10 s — et la liste retournée
est vide. Le contrat est inchangé : jamais d'exception propagée, la
stabilité de la découverte est prioritaire sur le signalement.
"""

from __future__ import annotations

import asyncio
import logging

from .adb import AdbDevice
from .shell import ShellAdapter, ShellResult

logger = logging.getLogger(__name__)

# Extrait de stderr conservé dans le WARNING (le reste n'apporte rien)
_STDERR_MAX = 120


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
        self._unavailable_warned = False

    def _warn_unavailable(self, detail: str) -> None:
        """Prévenir une seule fois par instance : la découverte tourne toutes les ~10 s."""
        if self._unavailable_warned:
            return
        self._unavailable_warned = True
        logger.warning(
            "ADB indisponible : %s (binaire utilisé : %s). Hypothèse la plus "
            "probable : le binaire adb est absent ou non autorisé dans "
            "l'image/système — vérifier l'installation du paquet `adb` et la "
            "variable BROXY4G_ADB_PATH.",
            detail,
            self.adb_path,
        )

    async def list_devices(self) -> list[AdbDevice]:
        try:
            result = await self.shell.run([self.adb_path, "devices", "-l"])
        except OSError as exc:
            # Binaire introuvable ou non exécutable (le FileNotFoundError de
            # RealShell est déjà codé 127 ; ici : PermissionError, etc.)
            self._warn_unavailable(f"exécution impossible ({exc.__class__.__name__}: {exc})")
            return []
        if result.returncode != 0:
            detail = f"code retour {result.returncode}"
            stderr = result.stderr.strip()
            if stderr:
                detail += f" (stderr : {stderr[:_STDERR_MAX]})"
            self._warn_unavailable(detail)
            return []
        return _parse_devices(result.stdout)

    async def _shell(self, serial: str, *command: str) -> ShellResult:
        return await self.shell.run([self.adb_path, "-s", serial, "shell", *command])

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
