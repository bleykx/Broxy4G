"""Real ModemManager adapter using mmcli (1.25.x / Huawei E3372)."""

from __future__ import annotations

import asyncio
import logging
import os
import re

from .mmcli_output import (
    apply_mmcli_signal_detail,
    parse_mmcli_modem,
    parse_mmcli_signal,
    parse_mmcli_sim,
)
from .modem_bearer import (
    bearer_connected,
    connected_bearer_paths,
    get_bearer_ip,
    parse_key_values,
)
from .modem_manager import BearerIpConfig, ModemInfo, SignalQuality
from .shell_real import ShellAdapter

logger = logging.getLogger(__name__)

# Signal refresh period (seconds) applied via ``mmcli --signal-setup`` when
# the modem has polling disabled (``modem.signal.refresh.rate == "0"``).
SIGNAL_POLL_SECONDS = 30


class RealModemManager:
    """Wraps mmcli for Huawei E3372 dongles (mmcli 1.25.x)."""

    def __init__(
        self,
        shell: ShellAdapter,
        disconnect_wait_attempts: int = 10,
        disconnect_wait_delay: float = 1.0,
    ):
        self.shell = shell
        self._disconnect_wait_attempts = disconnect_wait_attempts
        self._disconnect_wait_delay = disconnect_wait_delay

    # -- public API ----------------------------------------------------------

    async def list_modems(self) -> list[ModemInfo]:
        result = await self.shell.run(["mmcli", "-L"])
        if result.returncode != 0:
            return []
        paths = re.findall(r"/org/freedesktop/ModemManager1/Modem/\d+", result.stdout)
        modems: list[ModemInfo] = []
        for path in paths:
            modem = await self.get_modem(path)
            if modem:
                modems.append(modem)
        return modems

    async def get_modem(self, modem_path: str) -> ModemInfo | None:
        result = await self.shell.run(["mmcli", "-m", modem_path])
        if result.returncode != 0:
            return None

        # Wrap instance methods so monkeypatching works in tests
        def read_serial(dp: str | None) -> str | None:
            return self._read_usb_serial_from_sysfs(dp)

        def resolve_iface(dp: str | None) -> str | None:
            return self._resolve_iface_from_sysfs(dp)

        modem = parse_mmcli_modem(
            result.stdout,
            read_usb_serial=read_serial,
            resolve_interface=resolve_iface,
        )
        modem.path = modem_path

        # SIM fields (ICCID, IMSI, operator fallback) from the SIM if available
        sim_fields = await self._fetch_sim_fields(result.stdout)
        if modem.iccid is None:
            modem.iccid = sim_fields.get("iccid")
        modem.imsi = sim_fields.get("imsi")
        if not modem.operator_id:
            modem.operator_id = sim_fields.get("operator id")
        if not modem.operator_name:
            modem.operator_name = sim_fields.get("operator name")

        return modem

    async def connect(self, modem_path: str, apn: str, auth: str | None) -> None:
        if apn:
            value = f"apn={apn}"
            if auth and auth.lower() != "none":
                value += f",auth={auth}"
            cmd = ["mmcli", "-m", modem_path, f"--simple-connect={value}"]
        else:
            cmd = ["mmcli", "-m", modem_path, "--simple-connect"]
        result = await self.shell.run(cmd, timeout=120)
        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"mmcli connect failed (rc={result.returncode}): {msg}")

    async def disconnect(self, modem_path: str) -> None:
        """Déconnecte le bearer et attend qu'il soit effectivement tombé.

        Sans cette attente, ``--simple-connect`` réutilise le bearer existant
        (même session data / même IP publique). Un rc non nul (déjà
        déconnecté) est toléré : seul l'état final du bearer compte.
        """
        result = await self.shell.run(["mmcli", "-m", modem_path, "--simple-disconnect"])
        if result.returncode != 0:
            logger.warning(
                "simple-disconnect rc=%s sur %s (déjà déconnecté possible)",
                result.returncode, modem_path,
            )
        for attempt in range(self._disconnect_wait_attempts):
            if not await self._connected_bearer_paths(modem_path):
                return
            if attempt < self._disconnect_wait_attempts - 1:
                await asyncio.sleep(self._disconnect_wait_delay)
        raise RuntimeError(f"bearer toujours actif après simple-disconnect sur {modem_path}")

    async def reset_modem(self, modem_path: str) -> None:
        """Vrai reset modem : ré-énumération USB, seul moyen de changer l'IP publique.

        Le modem disparaît de ModemManager puis réapparaît (nouveau chemin,
        interface parfois renommée). L'identité IMEI permet de le retrouver.
        """
        result = await self.shell.run(["mmcli", "-m", modem_path, "--reset"], timeout=60)
        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"mmcli reset failed (rc={result.returncode}): {msg}")

    async def get_signal(self, modem_path: str) -> SignalQuality:
        result = await self.shell.run(["mmcli", "-m", modem_path])
        if result.returncode != 0:
            return SignalQuality()
        quality = parse_mmcli_signal(result.stdout)
        detail = await self.shell.run(["mmcli", "-m", modem_path, "--signal-get", "-K"])
        if detail.returncode != 0:
            return quality
        fields = parse_key_values(detail.stdout)
        if fields.get("modem.signal.refresh.rate") == "0":
            # Best-effort: enable signal polling. Failures must not break the read.
            await self.shell.run(
                ["mmcli", "-m", modem_path, "--signal-setup", str(SIGNAL_POLL_SECONDS)]
            )
        return apply_mmcli_signal_detail(quality, fields)

    async def get_imei(self, modem_path: str) -> str | None:
        modem = await self.get_modem(modem_path)
        return modem.imei if modem else None

    async def get_iccid(self, modem_path: str) -> str | None:
        modem = await self.get_modem(modem_path)
        return modem.iccid if modem else None

    async def get_bearer_ip(self, modem_path: str) -> BearerIpConfig | None:
        return await get_bearer_ip(self.shell, modem_path)

    async def get_session_id(self, modem_path: str) -> str | None:
        bearer = await self.get_bearer_ip(modem_path)
        return bearer.path if bearer else None

    # -- helpers -------------------------------------------------------------

    async def _bearer_connected(self, bearer_path: str) -> bool:
        return await bearer_connected(self.shell, bearer_path)

    async def _connected_bearer_paths(self, modem_path: str) -> list[str]:
        return await connected_bearer_paths(self.shell, modem_path)

    async def _fetch_sim_fields(self, modem_output: str) -> dict[str, str]:
        """Lance ``mmcli -i <sim-path>`` et retourne les champs SIM parsés.

        Le chemin SIM est extrait de la sortie ``mmcli -m`` déjà disponible.
        Retourne un dict vide si aucun SIM n'est rattaché ou en cas d'échec.
        """
        sim_match = re.search(r"primary sim path:\s*(\S+)", modem_output)
        if not sim_match:
            return {}
        sim_result = await self.shell.run(["mmcli", "-i", sim_match.group(1)])
        if sim_result.returncode != 0:
            return {}
        return parse_mmcli_sim(sim_result.stdout)

    @staticmethod
    def _read_usb_serial_from_sysfs(device_path: str | None) -> str | None:
        """Read /sys/bus/usb/devices/<dev>/serial (or return None)."""
        m = re.search(r"usb\d+/([\d-]+)", device_path or "")
        if not m:
            return None
        serial_file = f"/sys/bus/usb/devices/{m.group(1)}/serial"
        try:
            with open(serial_file) as f:
                return f.read().strip()
        except OSError:
            return None

    @staticmethod
    def _resolve_iface_from_sysfs(device_path: str | None) -> str | None:
        """Find first /sys/class/net/* whose realpath starts with device_path."""
        if not device_path:
            return None
        try:
            for name in os.listdir("/sys/class/net"):
                link = os.path.realpath(f"/sys/class/net/{name}")
                if link.startswith(device_path):
                    return name
        except OSError:
            pass
        return None
