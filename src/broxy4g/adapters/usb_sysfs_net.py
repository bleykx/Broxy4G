"""Netdev RNDIS d'un périphérique USB, résolu dans sysfs.

Mapping validé sur hôte réel :

- ``/sys/bus/usb/devices/1-2/serial`` == le serial ADB
  (``getprop ro.serialno``) ;
- la netdev vit sous ``/sys/bus/usb/devices/1-2/1-2:1.0/net/enx02360c7c147a``.

La netdev RNDIS est nommée ``enx<mac>`` et son nom n'est **pas** stable :
elle est donc résolue par sysfs, jamais par un nom codé en dur.

Piège mesuré sur hôte réel (Pi, téléphone Galaxy, serial ``R58N729DZMV``,
device ``1-1.2.4.2``) : le répertoire du device USB contient des liens
symboliques ``driver`` (pointant vers le répertoire du pilote, qui liste
**tous** les devices USB liés à ce pilote), ``subsystem`` (pointant vers
``/sys/bus/usb``, donc **tout** le bus) et ``port`` (renvoyant vers le
device lui-même, cf. ``port/device``). ``Path.glob`` suit ces liens, donc
les anciens motifs ``*/net``… renvoyaient **10 entrées pour 3 interfaces
réelles**, y compris les netdevs de **deux autres dongles 4G de l'hôte**
(``dongle-1``, ``dongle-4``) : la sélection avait alors retenu
``dongle-1``, un device inexistant pour le profil. L'exploration ne suit
donc **aucun** lien symbolique et dédoublonne par nom d'interface.

Module pur, sans effet de bord : pas de shell ni subprocess, uniquement des
lectures de fichiers via ``pathlib`` ; la racine sysfs est injectable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Racine sysfs des devices USB (liste de répertoires ``<id>``).
USB_DEVICES_ROOT = "/sys/bus/usb/devices"

# Profondeurs possibles du répertoire ``net`` sous le device USB (port direct,
# ou device imbriqué derrière un hub) : jamais plus de 3 niveaux.
_MAX_NET_DEPTH = 3


@dataclass(frozen=True)
class UsbNetInterface:
    name: str
    mac: str | None = None
    operstate: str | None = None


def find_usb_device_dir(serial: str, sysfs_root: str = USB_DEVICES_ROOT) -> str | None:
    """Répertoire sysfs du device USB dont le fichier ``serial`` vaut ``serial`` (strippé)."""
    root = Path(sysfs_root)
    if not root.is_dir():
        return None
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        serial_file = entry / "serial"
        try:
            if serial_file.is_file() and serial_file.read_text().strip() == serial:
                return str(entry)
        except OSError:
            continue
    return None


def _read_optional(path: Path) -> str | None:
    try:
        if path.is_file():
            return path.read_text().strip() or None
    except OSError:
        pass
    return None


def _find_net_dirs(device_dir: Path) -> list[Path]:
    """Répertoires ``net`` à ≤ ``_MAX_NET_DEPTH`` niveaux, sans suivre de lien.

    Les liens symboliques du device USB (``driver``, ``subsystem``, ``port``)
    sont ignorés : en les suivant, on ré-liste d'autres devices du même
    pilote ou du bus entier (piège mesuré, voir docstring du module).
    """
    found: list[Path] = []
    _walk_for_net_dirs(device_dir, depth=0, found=found)
    return found


def _walk_for_net_dirs(dir_path: Path, depth: int, found: list[Path]) -> None:
    if depth >= _MAX_NET_DEPTH:
        return
    try:
        entries = sorted(dir_path.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            continue  # jamais de lien symbolique : driver/subsystem/port
        if entry.name == "net":
            found.append(entry)
        else:
            _walk_for_net_dirs(entry, depth + 1, found)


def _collect_net_interfaces(device_dir: Path) -> list[UsbNetInterface]:
    seen: set[str] = set()
    interfaces: list[UsbNetInterface] = []
    for net_dir in _find_net_dirs(device_dir):
        try:
            iface_entries = sorted(net_dir.iterdir())
        except OSError:
            continue
        for iface in iface_entries:
            # Les entrées de ``net/`` sont elles-mêmes des liens symboliques
            # vers ``/sys/class/net/...`` : on les suit, c'est normal.
            if not iface.is_dir() or iface.name in seen:
                continue
            seen.add(iface.name)  # un nom de netdev ne peut exister qu'une fois
            interfaces.append(
                UsbNetInterface(
                    name=iface.name,
                    mac=_read_optional(iface / "address"),
                    operstate=_read_optional(iface / "operstate"),
                )
            )
    return sorted(interfaces, key=lambda iface: (iface.operstate != "up", iface.name))


def find_net_interfaces_for_usb_serial(
    serial: str, sysfs_root: str = USB_DEVICES_ROOT
) -> list[UsbNetInterface]:
    """Interfaces réseau du device USB portant ``serial``, ``up`` d'abord. ``[]`` si inconnu."""
    device_dir = find_usb_device_dir(serial, sysfs_root)
    if device_dir is None:
        return []
    return _collect_net_interfaces(Path(device_dir))
