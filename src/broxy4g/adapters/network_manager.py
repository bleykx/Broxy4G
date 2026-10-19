"""Adaptateur NetworkManager pour l'uplink RNDIS d'un smartphone.

Facts mesurés sur hôte réel (client 1.52 / démon 1.46) :
- Écriture du profil : **D-Bus brut** via ``gdbus``
  (``Settings.AddConnection``), PAS ``nmcli con add``/``con mod`` : le client
  libnm du conteneur sérialise ``mac-address-denylist``, inconnu du démon de
  l'hôte (``unknown property``). Formes du payload GVariant mesurées et
  normalisation de la relecture ``nmcli -g`` : ``nm_profile_payload.py``.
- Lecture (``-g``, ``-f DHCP4``) et activation (``con up``) : ``nmcli``.
- Le profil est lié à l'**identité matérielle** du netdev
  (``802-3-ethernet.mac-address``, type ``ay``), pas au nom d'interface
  (instable) : sinon une connexion NM auto pré-existante épinglée par
  ``interface-name`` reste candidate et fuit une route par défaut dans
  ``main`` (cf. ``nm_foreign_connections.py``, ``docs/networking.md``).
  Repli : MAC illisible ou invalide → ``connection.interface-name`` + warning.
- Les connexions NM **étrangères** de l'interface/MAC sont **supprimées** par
  ``ForeignConnectionDeleter`` (D-Bus Delete sur le ``DBUS-PATH`` fourni par
  ``nmcli``) : l'écriture partielle est impossible sous le décalage
  client/démon et notre profil lié par MAC empêche NM de recréer une auto.

Toutes les commandes passent par ``ShellAdapter`` (jamais de ``subprocess``).
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from .network_manager_cli_output import (
    NmIpv4,
    parse_dhcp_gateway,
    parse_ipv4_address,
)
from .nm_foreign_connections import ForeignConnectionDeleter
from .nm_profile_payload import (
    ProfileBinding,
    build_profile_variant,
    check_profile_values,
    parse_mac_bytes,
)
from .shell import ShellAdapter, ShellResult

logger = logging.getLogger(__name__)

# Métrique de la route du profil smartphone : élevée, jamais par défaut.
PROFILE_METRIC = 21000


@runtime_checkable
class NetworkManagerAdapter(Protocol):
    async def ensure_profile(self, interface: str, mac: str | None, name: str, metric: int = PROFILE_METRIC) -> bool:
        ...

    async def remove_foreign_connections(self, interface: str, mac: str | None, own_profile: str) -> None:
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

    async def profile_ipv4(self, name: str) -> NmIpv4 | None:
        ...

    async def dhcp_gateway(self, name: str) -> str | None:
        ...


class RealNetworkManager:
    def __init__(self, shell: ShellAdapter, nmcli_path: str = "nmcli", gdbus_path: str = "gdbus"):
        self.shell = shell
        self.nmcli = nmcli_path
        self.gdbus = gdbus_path
        self._unavailable_warned = False
        self._deleter = ForeignConnectionDeleter(shell, nmcli_path, gdbus_path)

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

    async def ensure_profile(self, interface: str, mac: str | None, name: str, metric: int = PROFILE_METRIC) -> bool:
        """Créer/réécrire le profil RNDIS, puis vérifier (fail-closed identité).

        ``mac=None`` ou invalide → repli ``connection.interface-name`` +
        warning, jamais d'exception. Vérification post-écriture en échec avec
        **identité lue positive et fausse** → suppression du profil écrit
        (défaut d'isolation) ; échec non concluant → ``False`` sans suppression.
        """
        binding = build_profile_variant(name, interface, mac, metric)
        if mac is None:
            logger.warning(
                "profil « %s » : MAC de %s illisible — repli sur "
                "connection.interface-name (identité instable, les connexions NM "
                "auto restent candidates sur l'interface)",
                name,
                interface,
            )
        elif parse_mac_bytes(mac) is None:
            logger.warning(
                "profil « %s » : MAC « %s » de %s invalide (≠ 6 octets hex) — repli sur "
                "connection.interface-name (identité instable)", name, mac, interface,
            )
        if await self.profile_exists(name):
            ok, _ = await self._verify_profile(name, metric, binding)
            if ok:
                return True  # profil déjà conforme : ne pas secouer le bearer
            await self.delete_profile(name)  # AddConnection refuse d'écraser un profil existant
        if not await self._write_profile(binding.variant):
            return False
        ok, identity_wrong = await self._verify_profile(name, metric, binding)
        if ok:
            return True
        if identity_wrong:
            logger.warning(
                "profil « %s » : défaut d'isolation concluant (identité ≠ « %s ») — "
                "suppression du profil écrit (activable sur une autre interface)", name, binding.expected_identity,
            )
            await self.delete_profile(name)
        return False

    async def _write_profile(self, variant: str) -> bool:
        """Création par ``gdbus`` ; payload : ``nm_profile_payload.build_profile_variant``."""
        result = await self._run([
            self.gdbus, "call", "--system",
            "--dest", "org.freedesktop.NetworkManager",
            "--object-path", "/org/freedesktop/NetworkManager/Settings",
            "--method", "org.freedesktop.NetworkManager.Settings.AddConnection",
            variant,
        ])
        return result.returncode == 0  # la sortie (objectpath '…') n'a pas à être parsée : activation par nom

    async def _verify_profile(self, name: str, metric: int, binding: ProfileBinding) -> tuple[bool, bool]:
        """Re-lit le profil : ``(conforme, identité positive et fausse)`` ; écart
        hors identité ou échec ``nmcli`` → non concluant (pas de destruction)."""
        result = await self._run([self.nmcli, "-g", binding.verify_fields, "con", "show", name])
        if result.returncode != 0:
            return False, False
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        expected = ["auto", "yes", "disabled", str(metric), "yes", binding.expected_identity]
        return check_profile_values(values, expected)

    async def remove_foreign_connections(self, interface: str, mac: str | None, own_profile: str) -> None:
        """Supprime les connexions NM étrangères de l'interface/MAC (jamais d'active).

        Délégation à ``ForeignConnectionDeleter`` ; aucune exception
        n'est propagée (NM en erreur → avertissement, le service continue).
        """
        await self._deleter.delete_foreign(interface, mac, own_profile)

    async def up(self, name: str) -> bool:
        return (await self._run([self.nmcli, "con", "up", name])).returncode == 0

    async def down(self, name: str) -> bool:
        return (await self._run([self.nmcli, "con", "down", name])).returncode == 0

    async def delete_profile(self, name: str) -> bool:
        return (await self._run([self.nmcli, "con", "delete", name])).returncode == 0

    async def device_ipv4(self, interface: str) -> NmIpv4 | None:
        """Première adresse du device, toutes ip-configs confondues (``addr/prefix``).

        Vue device : mélange les adresses de toutes les connexions du device ;
        parsing robuste (jamais d'exception, ``network_manager_cli_output``).
        """
        result = await self._run([self.nmcli, "-g", "IP4.ADDRESS", "device", "show", interface])
        if result.returncode != 0:
            return None
        return parse_ipv4_address(result.stdout)

    async def profile_ipv4(self, name: str) -> NmIpv4 | None:
        """Présence d'une adresse IPv4 sur le profil ; ``None`` sinon.

        **Porte de présence** du chemin smartphone (fail-closed : pas
        d'adresse → l'appelant force le plancher). La **valeur** de l'adresse
        du bearer ne vient pas d'ici (ordre imprimé non fiable) : le noyau.
        """
        result = await self._run([self.nmcli, "-g", "IP4.ADDRESS", "con", "show", name])
        if result.returncode != 0:
            return None
        return parse_ipv4_address(result.stdout)

    async def dhcp_gateway(self, name: str) -> str | None:
        """Première passerelle de ``nmcli -f DHCP4 con show <name>`` (``routers = <ip>``)."""
        result = await self._run([self.nmcli, "-f", "DHCP4", "con", "show", name])
        if result.returncode != 0:
            return None
        return parse_dhcp_gateway(result.stdout)


class FakeNetworkManager:
    """Stub en mémoire, sans ShellAdapter (``calls`` journalise les opérations)."""

    def __init__(self):
        self.profiles: dict[str, dict] = {}
        self.devices: dict[str, NmIpv4] = {}
        self.profile_ips: dict[str, NmIpv4] = {}
        self.gateways: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.up_result: bool = True

    async def ensure_profile(self, interface: str, mac: str | None, name: str, metric: int = PROFILE_METRIC) -> bool:
        self.calls.append(["ensure_profile", interface, mac, name, str(metric)])
        self.profiles[name] = {
            "interface": interface,
            "mac": mac,
            "metric": metric,
            "ipv4.method": "auto",
            "ipv4.never-default": "yes",
            "ipv6.method": "disabled",
            "connection.autoconnect": "yes",
        }
        return True

    async def remove_foreign_connections(self, interface: str, mac: str | None, own_profile: str) -> None:
        self.calls.append(["remove_foreign", interface, mac, own_profile])

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

    async def profile_ipv4(self, name: str) -> NmIpv4 | None:
        return self.profile_ips.get(name)

    async def dhcp_gateway(self, name: str) -> str | None:
        return self.gateways.get(name)
