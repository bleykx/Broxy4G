"""Netdev RNDIS d'un périphérique USB, résolu dans sysfs.

Mapping validé sur hôte réel :

- ``/sys/bus/usb/devices/1-2/serial`` == le serial ADB
  (``getprop ro.serialno``) ;
- la netdev vit sous ``/sys/bus/usb/devices/1-2/1-2:1.0/net/enx02360c7c147a``.

La netdev RNDIS est nommée ``enx<mac>`` et son nom n'est **pas** stable :
elle est donc résolue par sysfs, jamais par un nom codé en dur.

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
_NET_GLOBS = ("*/net", "*/*/net", "*/*/*/net")


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


def _collect_net_interfaces(device_dir: Path) -> list[UsbNetInterface]:
    interfaces: list[UsbNetInterface] = []
    for pattern in _NET_GLOBS:
        for net_dir in device_dir.glob(pattern):
            if not net_dir.is_dir():
                continue
            for iface in sorted(net_dir.iterdir()):
                if not iface.is_dir():
                    continue
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
