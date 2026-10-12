"""Accès sysfs à l'autorisation USB (ré-énumération douce).

Écrire ``0`` puis ``1`` dans le fichier ``authorized`` d'un périphérique USB
de sysfs fait que le noyau le désattache et le ré-énumère proprement, sans
reboot. C'est le mécanisme de récupération douce des dongles bloqués en
``ERROR``/``OFFLINE`` avec un bearer PPP (port ``net`` absent chez
ModemManager, donc aucun netdev).

Le chemin du périphérique USB est rapporté par ModemManager (champ ``device``
de ``mmcli -m``, section System) : il existe même quand aucun netdev n'est
présent. Seule écriture possible : ``<root>/<usb-id>/authorized``, dont le
répertoire se résout (realpath) dans un vrai répertoire de device USB
(``…/usbN/<usb-id>``) — toute autre cible est refusée.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Protocol, runtime_checkable

# Fragment d'identifiant de device USB dans un chemin sysfs :
# usb1/1-7 → "1-7", usb1/1-1.2.4 → "1-1.2.4" (chiffres, tirets ET points :
# les dongles multifonction sont des sous-périphériques multi-niveaux).
_USB_DEV_RE = re.compile(r"usb\d+/([\d.-]+)")

USB_DEVICES_ROOT = "/sys/bus/usb/devices"


@runtime_checkable
class UsbDeviceAdapter(Protocol):
    async def resolve_authorized_file(self, device_path: str | None) -> str | None:
        """Chemin du fichier ``authorized`` du device, ou None si introuvable."""
        ...

    async def read_authorized(self, authorized_file: str) -> bool:
        ...

    async def write_authorized(self, authorized_file: str, authorized: bool) -> None:
        ...


class RealUsbDevice:
    """Lecture/écriture ``authorized`` dans sysfs, avec borne de chemin.

    La borne : le device est extrait du chemin par la regex ``usbN/ID``
    (l'ID est limité à chiffres/points/tirets, donc impossible d'infiltrer
    ``..`` ou ``/``), puis le
    répertoire ``<root>/<ID>`` doit exister ET se résoudre (realpath) dans un
    vrai sous-arbre USB (``…/usbN/<ID>``) contenant bien le fichier
    ``authorized``.
    """

    def __init__(self, root: str = USB_DEVICES_ROOT):
        self._root = root

    async def resolve_authorized_file(self, device_path: str | None) -> str | None:
        if not device_path:
            return None
        m = _USB_DEV_RE.search(device_path)
        if not m:
            return None
        dev_id = m.group(1)
        device_dir = os.path.realpath(os.path.join(self._root, dev_id))
        if not re.fullmatch(rf".*usb\d+/{re.escape(dev_id)}", device_dir):
            return None
        authorized_file = os.path.join(device_dir, "authorized")
        return authorized_file if os.path.isfile(authorized_file) else None

    async def read_authorized(self, authorized_file: str) -> bool:
        with open(authorized_file) as f:
            return f.read().strip() == "1"

    async def write_authorized(self, authorized_file: str, authorized: bool) -> None:
        with open(authorized_file, "w") as f:
            f.write("1" if authorized else "0")


class FakeUsbDevice:
    """Fake d'autorisation USB pour les tests.

    ``register(device_path)`` déclare un device visible avec son fichier
    ``authorized``. L'hook ``on_reenumeration`` est appelé lors d'une
    ré-autorisation : les tests y simulent la ré-énumération noyau (ex. le
    modem disparaît puis réapparaît chez ``FakeModemManager``).
    """

    def __init__(self):
        self._files: dict[str, str] = {}  # usb id → chemin du fichier authorized
        self._state: dict[str, bool] = {}
        self.writes: list[tuple[str, bool]] = []
        self.on_reenumeration: Callable[[], None] | None = None

    def register(self, device_path: str | None, authorized: bool = True) -> str | None:
        m = _USB_DEV_RE.search(device_path or "")
        if not m:
            return None
        path = f"{USB_DEVICES_ROOT}/{m.group(1)}/authorized"
        self._files[m.group(1)] = path
        self._state[path] = authorized
        return path

    def set_authorized(self, authorized_file: str, value: bool) -> None:
        self._state[authorized_file] = value

    async def resolve_authorized_file(self, device_path: str | None) -> str | None:
        m = _USB_DEV_RE.search(device_path or "")
        if not m:
            return None
        return self._files.get(m.group(1))

    async def read_authorized(self, authorized_file: str) -> bool:
        return self._state.get(authorized_file, False)

    async def write_authorized(self, authorized_file: str, authorized: bool) -> None:
        self.writes.append((authorized_file, authorized))
        if authorized_file in self._state:
            self._state[authorized_file] = authorized
        if authorized and self.on_reenumeration is not None:
            self.on_reenumeration()
