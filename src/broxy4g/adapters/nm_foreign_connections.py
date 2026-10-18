"""Suppression des connexions NM étrangères d'un profil RNDIS smartphone.

Une connexion NM pré-existante liée à la même interface (``Wired connection N``
créée par NM, épinglée par ``interface-name``) ou au même MAC reste **candidate**
après l'apparition de notre profil et réinstalle sa propre route par défaut dans
la table ``main`` à chaque (ré)activation (fuite mesurée : ``default … dev enx…
metric 2023`` à chaque cycle mode avion du reset). Le ``ipv4.never-default`` de
notre profil ne la supprime jamais (NM ne supprime que la route automatique de
sa propre connexion).

**Pourquoi supprimer et non « neutraliser »** (mesuré, client nmcli 1.52.1 /
démon NM 1.46.0) :
- toute **écriture partielle** ``nmcli con mod`` échoue sous le décalage
  client/démon (``802-3-ethernet.mac-address-denylist: unknown property``) —
  impossible de poser ``autoconnect=no``/``never-default=yes`` ;
- la méthode D-Bus ``Settings.Connection.Update`` **remplace toutes les
  propriétés** : un map partiel détruirait (ou ferait rejeter) la connexion —
  à ne pas utiliser ;
- le chemin d'objet ne peut être **reconstruit** à partir du UUID (un tiret est
  illégal dans un chemin d'objet D-Bus : ``… : not a valid object path``) : on
  prend le champ ``DBUS-PATH`` fourni par ``nmcli`` ;
- NM ne supprime **jamais** une connexion auto pré-existante quand notre profil
  apparaît ensuite, mais notre profil est désormais lié par **MAC** : tant
  qu'il existe, NM ne recrée pas de connexion auto → la suppression converge.

Suppression D-Bus ``Settings.Connection.Delete`` sur le ``DBUS-PATH`` de la
connexion. Idempotente (absence = no-op silencieux), ne touche **que** les
connexions de notre périmètre (``interface-name``/MAC == notre interface, type
``802-3-ethernet``, uuid ≠ notre profil) et **jamais** une connexion active.
Aucune exception propagée : NM en erreur → avertissement, le service continue.
"""

from __future__ import annotations

import logging

from .network_manager_cli_output import (
    NmConnectionDetail,
    parse_active_uuids,
    parse_connection_detail,
    split_nmcli_t,
)
from .shell import ShellAdapter

logger = logging.getLogger(__name__)

# Champs d'énumération : uniquement des champs **autorisés** par ``con show``
# (IFNAME n'existe pas → la commande échouerait entièrement).
_LIST_FIELDS = "NAME,UUID,TYPE,DBUS-PATH"
_ACTIVE_FIELDS = "UUID,DEVICE"
_DETAIL_FIELDS = "connection.interface-name,connection.uuid,802-3-ethernet.mac-address"
_DELETE_METHOD = "org.freedesktop.NetworkManager.Settings.Connection.Delete"


class ForeignConnectionDeleter:
    """Supprime les connexions NM étrangères de notre interface/MAC (jamais d'active)."""

    def __init__(self, shell: ShellAdapter, nmcli_path: str = "nmcli", gdbus_path: str = "gdbus"):
        self.shell = shell
        self.nmcli = nmcli_path
        self.gdbus = gdbus_path

    async def delete_foreign(self, interface: str, mac: str | None, own_profile: str) -> None:
        """Supprime chaque connexion étrangère liée à ``interface``/``mac``.

        Sélection : ``connection.interface-name`` == notre interface **ou**
        ``802-3-ethernet.mac-address`` == notre MAC, et type ``802-3-ethernet``,
        et uuid ≠ celui de notre profil (résolu par son nom). Fail-safe : si
        notre uuid n'est pas résolu, **aucune** suppression (on ne peut plus
        distinguer notre propre connexion). Jamais d'exception.
        """
        own_uuid = await self._own_uuid(own_profile)
        if not own_uuid:
            logger.warning(
                "uplink smartphone : uuid du profil « %s » introuvable — "
                "suppression des connexions NM étrangères ignorée (fail-safe)",
                own_profile,
            )
            return
        listing = await self.shell.run([self.nmcli, "-t", "-f", _LIST_FIELDS, "con", "show"])
        if listing.returncode != 0:
            logger.warning(
                "uplink smartphone : lecture des connexions NM impossible (rc=%s) — "
                "suppression des connexions étrangères ignorée",
                listing.returncode,
            )
            return
        active = await self._active_uuids()
        for line in listing.stdout.splitlines():
            if not line.strip():
                continue
            fields = split_nmcli_t(line)
            if len(fields) < 4:
                continue  # ligne illisible : ignorée, jamais d'exception
            name, uuid, ctype, dbus_path = fields[:4]
            if ctype != "802-3-ethernet" or uuid == own_uuid:
                continue  # notre propre profil / autre type
            detail = await self._detail(uuid)
            if detail is None:
                continue
            if not self._matches(detail, interface, mac):
                continue
            if uuid in active:
                logger.warning(
                    "ACTION ADMIN : connexion étrangère active « %s » (uuid %s) "
                    "sur %s ignorée",
                    name,
                    uuid,
                    interface,
                )
                continue
            if not await self._delete(dbus_path):
                logger.warning(
                    "uplink smartphone : suppression de la connexion étrangère « %s » "
                    "(uuid %s) en échec",
                    name,
                    uuid,
                )
                continue
            logger.warning(
                "ACTION ADMIN : connexion NM étrangère « %s » (uuid %s) sur %s supprimée",
                name,
                uuid,
                interface,
            )

    @staticmethod
    def _matches(detail: NmConnectionDetail, interface: str, mac: str | None) -> bool:
        by_iface = detail.interface_name == interface
        by_mac = bool(mac and detail.mac and detail.mac.lower() == mac.lower())
        return by_iface or by_mac

    async def _own_uuid(self, name: str) -> str | None:
        """uuid de notre profil, résolu par son **nom** (jamais reconstruit)."""
        result = await self.shell.run([self.nmcli, "-g", "connection.uuid", "con", "show", name])
        if result.returncode != 0:
            return None
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return lines[0] if lines else None

    async def _active_uuids(self) -> set[str]:
        result = await self.shell.run([self.nmcli, "-t", "-f", _ACTIVE_FIELDS, "con", "show", "--active"])
        if result.returncode != 0:
            return set()
        return parse_active_uuids(result.stdout)

    async def _detail(self, uuid: str) -> NmConnectionDetail | None:
        result = await self.shell.run([self.nmcli, "-g", _DETAIL_FIELDS, "con", "show", uuid])
        if result.returncode != 0:
            return None
        return parse_connection_detail(result.stdout)

    async def _delete(self, dbus_path: str) -> bool:
        """Suppression D-Bus sur le ``DBUS-PATH`` fourni par ``nmcli`` (jamais reconstruit)."""
        if not dbus_path:
            return False
        result = await self.shell.run([
            self.gdbus, "call", "--system",
            "--dest", "org.freedesktop.NetworkManager",
            "--object-path", dbus_path,
            "--method", _DELETE_METHOD,
        ])
        return result.returncode == 0
