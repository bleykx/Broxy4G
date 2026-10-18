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
- L'adresse du profil (scope, fail-closed du chemin smartphone) se lit avec
  ``nmcli -g IP4.ADDRESS con show <name>``. La vue device
  (``device show <if>``) reste disponible mais mélange les adresses de toutes
  les ip-configs du device, multi-adresses comprises : le parsing robuste
  (jamais d'exception) est dans ``network_manager_cli_output``.
- Le profil est lié à l'**identité matérielle** du netdev
  (``802-3-ethernet.mac-address``), pas au nom d'interface (instable) : sinon
  une connexion NM auto pré-existante épinglée par ``interface-name`` reste
  candidate et fuit une route par défaut dans ``main`` (cf.
  ``nm_foreign_connections.py`` et ``docs/networking.md``). Repli : MAC
  illisible → comportement antérieur (``connection.interface-name``) + warning.
- Les connexions NM **étrangères** de l'interface/MAC (ex. ``Wired connection N``)
  sont **supprimées** par ``ForeignConnectionDeleter`` (D-Bus
  ``Settings.Connection.Delete`` sur le champ ``DBUS-PATH`` fourni par ``nmcli``) :
  l'écriture partielle est impossible sous le décalage client/démon et notre
  profil lié par MAC empêche NM de recréer une connexion auto.

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
from .shell import ShellAdapter, ShellResult

logger = logging.getLogger(__name__)

# Métrique de la route du profil smartphone : élevée, jamais par défaut.
PROFILE_METRIC = 21000

# Le profil est vérifié sur la MAC (identité matérielle), jamais sur le nom
# d'interface ; le 2e champ n'existe que pour le repli MAC illisible.
_VERIFY_FIELDS = "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,connection.autoconnect,802-3-ethernet.mac-address"
_VERIFY_FIELDS_IFACE = "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,connection.autoconnect,connection.interface-name"


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
        """Créer ou réécrire le profil RNDIS, puis vérifier.

        Le profil est lié par MAC (identité matérielle du netdev) ; ``mac=None``
        → repli tolérant sur ``connection.interface-name`` (comportement
        antérieur) avec un avertissement, jamais d'exception.
        Écriture par D-Bus brut : ``nmcli con add``/``con mod`` sont inutilisables
        sous décalage client/démon de versions (toutes les écritures échouaient
        avec ``… : unknown property``, cf. docstring).
        """
        if mac is None:
            logger.warning(
                "profil « %s » : MAC de %s illisible — repli sur "
                "connection.interface-name (identité instable, les connexions NM "
                "auto restent candidates sur l'interface)",
                name,
                interface,
            )
        if await self.profile_exists(name):
            if await self._verify_profile(name, metric, interface, mac):
                return True  # profil déjà conforme : ne pas secouer le bearer
            await self.delete_profile(name)  # AddConnection refuse d'écraser un profil existant
        if not await self._write_profile(interface, mac, name, metric):
            return False
        return await self._verify_profile(name, metric, interface, mac)

    async def _write_profile(self, interface: str, mac: str | None, name: str, metric: int) -> bool:
        """Création du profil par ``gdbus`` : seule la valeur du dict sérialisée part.

        Liaison au device : ``802-3-ethernet.mac-address`` (MAC, minuscules),
        jamais ``connection.interface-name`` — sauf repli MAC illisible.
        """
        if mac is not None:
            binding = f"'mac-address': <'{self._gquote(mac.lower())}'>"
        else:
            binding = f"'interface-name': <'{self._gquote(interface)}'>"
        variant = (
            f"{{'connection': {{'id': <'{self._gquote(name)}'>, 'type': <'802-3-ethernet'>, "
            f"{binding}, 'autoconnect': <true>}}, "
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

    async def _verify_profile(self, name: str, metric: int, interface: str, mac: str | None) -> bool:
        fields = _VERIFY_FIELDS if mac is not None else _VERIFY_FIELDS_IFACE
        result = await self._run([self.nmcli, "-g", fields, "con", "show", name])
        if result.returncode != 0:
            return False
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        # L'identité attendue compte : un device renommé (ou un profil périmé
        # sans MAC) doit déclencher une réécriture, pas garder l'ancien profil.
        expected = mac.lower() if mac is not None else interface
        return values == ["auto", "yes", "disabled", str(metric), "yes", expected]

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

        Vue device de ``nmcli -g IP4.ADDRESS device show <if>`` : mélange les
        adresses de toutes les connexions du device (notre bail et résiduelles),
        multi-adresses comprises, et l'ordre imprimé n'est pas celui du noyau.
        Parsing robuste (jamais d'exception) : ``None`` si aucune adresse.
        """
        result = await self._run([self.nmcli, "-g", "IP4.ADDRESS", "device", "show", interface])
        if result.returncode != 0:
            return None
        return parse_ipv4_address(result.stdout)

    async def profile_ipv4(self, name: str) -> NmIpv4 | None:
        """Présence d'une adresse IPv4 sur le profil (``addr/prefix``) ; ``None`` sinon.

        Lecture scopée au profil de ``nmcli -g IP4.ADDRESS con show <name>`` : ne
        renvoie JAMAIS l'adresse d'une autre (ou résiduelle) connexion du
        device — c'est la **porte de présence** du chemin smartphone (fail-closed :
        pas d'adresse → l'appelant force le plancher). La **valeur** de l'adresse
        du bearer ne vient pas d'ici : l'ordre imprimé par ``nmcli`` n'est pas
        fiable (deux adresses sur une ligne, ordre observé variable), elle est lue
        dans le noyau (``InterfaceAdapter.primary_ipv4``).
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
