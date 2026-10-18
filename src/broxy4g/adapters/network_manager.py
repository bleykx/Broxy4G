"""Adaptateur NetworkManager pour l'uplink RNDIS d'un smartphone.

Facts mesurés sur hôte réel :
- Écriture du profil (création/réécriture) : **D-Bus brut** via ``gdbus``
  (``Settings.AddConnection``), PAS ``nmcli con add``/``con mod``. Sous
  déploiement Docker, le client libnm du conteneur (Debian trixie, 1.52)
  sérialise systématiquement ``802-3-ethernet.mac-address-denylist``, que le
  démon NM de l'hôte (Ubuntu 24.04, 1.46) ne connaît pas : toutes les écritures
  nmcli échouaient avec ``… : unknown property`` (incompatibilité de schéma
  client/démon, pas de réglage). Une écriture D-Bus ne sérialise que les clés
  du dict → indépendante des versions.
- Lecture (``-g``, ``-f DHCP4``) et activation (``con up``) : ``nmcli``, qui
  fonctionne des deux côtés du décalage de versions.
- La passerelle DHCP se lit dans ``nmcli -f DHCP4 con show <name>``
  (``DHCP4.OPTION[27]:  routers = 192.168.172.245``), pas dans ``IP4.GATEWAY``.
- L'adresse runtime se lit avec ``nmcli -g IP4.ADDRESS device show <if>``
  (``192.168.172.78/24``), le profil ``con show`` n'a pas de section IP4.

Toutes les commandes passent par ``ShellAdapter`` (jamais de ``subprocess``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .shell import ShellAdapter, ShellResult

logger = logging.getLogger(__name__)

# Métrique de la route du profil smartphone : élevée, jamais par défaut.
PROFILE_METRIC = 21000

_VERIFY_FIELDS = "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,connection.autoconnect,connection.interface-name"
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
    def __init__(self, shell: ShellAdapter, nmcli_path: str = "nmcli", gdbus_path: str = "gdbus"):
        self.shell = shell
        self.nmcli = nmcli_path
        self.gdbus = gdbus_path
        self._unavailable_warned = False

    def _warn_unavailable(self, binary: str, detail: str) -> None:
        """Prévenir une seule fois par instance : les appels nmcli/gdbus sont fréquents."""
        if self._unavailable_warned:
            return
        self._unavailable_warned = True
        package = "libglib2.0-bin" if binary == self.gdbus else "network-manager"
        logger.warning(
            "%s introuvable : %s (binaire utilisé : %s). Le transport "
            "smartphone ADB (profil RNDIS) en a besoin ; dans l'image "
            "Docker le paquet à installer est `%s`.",
            binary,
            detail,
            binary,
            package,
        )

    async def _run(self, command: list[str]) -> ShellResult:
        """Toute commande nmcli/gdbus ; le code retour 127 (RealShell) = binaire absent."""
        result = await self.shell.run(command)
        if result.returncode == 127:
            self._warn_unavailable(command[0], result.stderr.strip() or "command not found")
        return result

    async def profile_exists(self, name: str) -> bool:
        result = await self._run([self.nmcli, "con", "show", name])
        return result.returncode == 0 and bool(result.stdout.strip())

    async def ensure_profile(self, interface: str, name: str, metric: int = PROFILE_METRIC) -> bool:
        """Créer ou réécrire le profil RNDIS, puis vérifier.

        Écriture par D-Bus brut : ``nmcli con add``/``con mod`` sont inutilisables
        sous décalage client/démon de versions (toutes les écritures échouaient
        avec ``… : unknown property``, cf. docstring).
        """
        if await self.profile_exists(name):
            if await self._verify_profile(name, metric, interface):
                return True  # profil déjà conforme : ne pas secouer le bearer
            await self.delete_profile(name)  # AddConnection refuse d'écraser un profil existant
        if not await self._write_profile(interface, name, metric):
            return False
        return await self._verify_profile(name, metric, interface)

    async def _write_profile(self, interface: str, name: str, metric: int) -> bool:
        """Création du profil par ``gdbus`` : seule la valeur du dict sérialisée part."""
        variant = (
            f"{{'connection': {{'id': <'{self._gquote(name)}'>, 'type': <'802-3-ethernet'>, "
            f"'interface-name': <'{self._gquote(interface)}'>, 'autoconnect': <true>}}, "
            f"'ipv4': {{'method': <'auto'>, 'never-default': <true>, "
            f"'route-metric': <{metric}>}}, 'ipv6': {{'method': <'disabled'>}}}}"
        )
        result = await self._run([
            self.gdbus, "call", "--system",
            "--dest", "org.freedesktop.NetworkManager",
            "--object-path", "/org/freedesktop/NetworkManager/Settings",
            "--method", "org.freedesktop.NetworkManager.Settings.AddConnection",
            variant,
        ])
        return result.returncode == 0  # la sortie (objectpath '…') n'a pas à être parsée : activation par nom

    @staticmethod
    def _gquote(value: str) -> str:
        """Échappe défensivement une chaîne du dict GVariant (guillemets simples)."""
        return value.replace("\\", "\\\\").replace("'", "\\'")

    async def _verify_profile(self, name: str, metric: int, interface: str) -> bool:
        result = await self._run([self.nmcli, "-g", _VERIFY_FIELDS, "con", "show", name])
        if result.returncode != 0:
            return False
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        # L'interface attendue compte : l'app résout le device dynamiquement, un
        # nom qui a changé doit déclencher une réécriture, pas garder un profil périmé.
        return values == ["auto", "yes", "disabled", str(metric), "yes", interface]

    async def up(self, name: str) -> bool:
        return (await self._run([self.nmcli, "con", "up", name])).returncode == 0

    async def down(self, name: str) -> bool:
        return (await self._run([self.nmcli, "con", "down", name])).returncode == 0

    async def delete_profile(self, name: str) -> bool:
        return (await self._run([self.nmcli, "con", "delete", name])).returncode == 0

    async def device_ipv4(self, interface: str) -> NmIpv4 | None:
        """Première adresse de ``nmcli -g IP4.ADDRESS device show <if>`` (``addr/prefix``)."""
        result = await self._run([self.nmcli, "-g", "IP4.ADDRESS", "device", "show", interface])
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
        result = await self._run([self.nmcli, "-f", "DHCP4", "con", "show", name])
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
