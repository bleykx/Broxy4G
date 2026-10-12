"""Lecture de l'état du bearer data via mmcli (session, IP, connectivité)."""

from __future__ import annotations

from .modem_manager import BearerIpConfig
from .shell_real import ShellAdapter


def parse_key_values(text: str) -> dict[str, str]:
    """Parse la sortie ``mmcli -K`` (``clé : valeur``) en dictionnaire."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


async def read_bearer(shell: ShellAdapter, bearer_path: str) -> BearerIpConfig | None:
    """Lis ``mmcli -b <path> -K`` ; None si le bearer n'est pas connecté."""
    result = await shell.run(["mmcli", "-b", bearer_path, "-K"])
    if result.returncode != 0:
        return None
    fields = parse_key_values(result.stdout)
    if fields.get("bearer.status.connected") != "yes":
        return None
    address = fields.get("bearer.ipv4-config.address")
    prefix_raw = fields.get("bearer.ipv4-config.prefix")
    if not address or address == "--" or not prefix_raw or prefix_raw == "--":
        if fields.get("bearer.ipv4-config.method") == "ppp":
            # Session PPP sans interface réseau (port net absent chez
            # ModemManager) : aucune IPv4 — le diagnostic est remonté.
            return BearerIpConfig(address="", prefix=0, method="ppp", path=bearer_path)
        return None
    gateway = fields.get("bearer.ipv4-config.gateway")
    interface = fields.get("bearer.status.interface")
    mtu_raw = fields.get("bearer.ipv4-config.mtu")
    return BearerIpConfig(
        address=address,
        prefix=int(prefix_raw),
        gateway=None if gateway in (None, "--") else gateway,
        dns=[v for k, v in fields.items() if k.startswith("bearer.ipv4-config.dns.value[")],
        interface=None if interface in (None, "--") else interface,
        mtu=int(mtu_raw) if mtu_raw and mtu_raw != "--" else None,
        path=bearer_path,
    )


async def bearer_connected(shell: ShellAdapter, bearer_path: str) -> bool:
    """True si ``bearer.status.connected`` vaut ``yes`` sur le bearer."""
    result = await shell.run(["mmcli", "-b", bearer_path, "-K"])
    if result.returncode != 0:
        return False
    return parse_key_values(result.stdout).get("bearer.status.connected") == "yes"


async def bearer_paths(shell: ShellAdapter, modem_path: str) -> list[str]:
    """Chemin des bearers rattachés au modem (``modem.generic.bearers.value[...]``)."""
    info = await shell.run(["mmcli", "-m", modem_path, "-K"])
    if info.returncode != 0:
        return []
    modem = parse_key_values(info.stdout)
    return sorted(
        value
        for key, value in modem.items()
        if key.startswith("modem.generic.bearers.value[")
        and value
        and value != "--"
    )


async def connected_bearer_paths(shell: ShellAdapter, modem_path: str) -> list[str]:
    """Bearers du modem actuellement ``connected``."""
    return [
        path
        for path in await bearer_paths(shell, modem_path)
        if await bearer_connected(shell, path)
    ]


async def get_bearer_ip(shell: ShellAdapter, modem_path: str) -> BearerIpConfig | None:
    """Configuration IPv4 du premier bearer connecté du modem, sinon None."""
    for bearer_path in await bearer_paths(shell, modem_path):
        config = await read_bearer(shell, bearer_path)
        if config is not None:
            return config
    return None


async def get_session_id(shell: ShellAdapter, modem_path: str) -> str | None:
    """Identifiant de la session data courante : le chemin du bearer connecté."""
    bearer = await get_bearer_ip(shell, modem_path)
    return bearer.path if bearer else None
