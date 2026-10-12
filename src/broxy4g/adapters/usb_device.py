"""Accès sysfs à l'autorisation USB (ré-énumération douce).

Écrire ``0`` puis ``1`` dans le fichier ``authorized`` d'un périphérique USB
de sysfs fait que le noyau le désattache et le ré-énumère proprement, sans
reboot. C'est le mécanisme de récupération douce des dongles bloqués en
``ERROR``/``OFFLINE`` avec un bearer PPP (port ``net`` absent chez
ModemManager, donc aucun netdev).

Le chemin du périphérique USB est rapporté par ModemManager (champ ``device``
de ``mmcli -m``, section System) : il existe même quand aucun netdev n'est
présent. Les topologies réelles sont souvent imbriquées (le dongle derrière
des hubs, ex. Pi : ``…/usb1/1-1/1-1.2/1-1.2.4``) : l'identifiant visé est
toujours le composant **le plus profond** (``adapters/usb_sysfs_path.py``),
et un hub USB (``bDeviceClass`` = ``09``) est **jamais** ciblé — le
désautorisé ferait tomber tous les périphériques en aval.

Seule écriture possible : ``<root>/<usb-id>/authorized``, dont le
répertoire se résout (realpath) dans l'arborescence d'un contrôleur USB
(``…/usbN/…``) avec pour dernier composant l'ID visé — toute autre cible
est refusée.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .usb_sysfs_path import USB_DEVICES_ROOT, resolve_usb_device_id

logger = logging.getLogger(__name__)


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
    """Lecture/écriture ``authorized`` dans sysfs, avec bornes de chemin.

    Résolution en trois bornes défensives :

    1. l'identifiant est le composant **le plus profond** du chemin rapporté
       (topologies imbriquées derrière des hubs : le dongle, jamais le hub) ;
    2. le répertoire ``<root>/<ID>`` doit se résoudre (realpath) dans
       l'arborescence d'un contrôleur USB (``…/usbN/…``, dernier composant =
       l'ID visé, imbriquement compris) et contenir le fichier ``authorized`` ;
    3. le device ne doit pas être un **hub** (``bDeviceClass`` = ``09``) :
       le désautorisé couperait tous les périphériques en aval.
    """

    def __init__(self, root: str = USB_DEVICES_ROOT):
        self._root = root

    async def resolve_authorized_file(self, device_path: str | None) -> str | None:
        dev_id = resolve_usb_device_id(device_path)
        if dev_id is None:
            return None
        device_dir = os.path.realpath(os.path.join(self._root, dev_id))
        # Borne 2 : le realpath est un vrai device USB — dernier composant =
        # l'ID visé, dans l'arborescence d'un contrôleur (``…/usbN/…``),
        # y compris imbriquée (``…/usb1/1-1/1-1.2/1-1.2.4``).
        if os.path.basename(device_dir) != dev_id or not re.search(
            r"/usb\d+/", device_dir
        ):
            return None
        authorized_file = os.path.join(device_dir, "authorized")
        if not os.path.isfile(authorized_file):
            return None
        if await self._is_usb_hub(device_dir):
            logger.warning(
                "Refus de cibler le périphérique USB %s : c'est un hub "
                "(bDeviceClass=09) — le désautorisé ferait tomber tous les "
                "périphériques en aval (dongle inclus)",
                dev_id,
            )
            return None
        return authorized_file

    async def _is_usb_hub(self, device_dir: str) -> bool:
        """True si ``device_dir`` est un hub USB (``bDeviceClass`` = ``09``).

        Fichier illisible (absent ou permissions) : on **ne refuse pas** —
        tout device USB du bus expose ce fichier, la borne realpath (2) reste
        active, et refuser ici rendrait la récupération inopérante sur tout
        sysfs atypique. L'anomalie est journalisée en DEBUG et la décision
        finale revient à la borne de chemin.
        """
        try:
            with open(os.path.join(device_dir, "bDeviceClass")) as f:
                return f.read().strip() == "09"
        except OSError:
            logger.debug(
                "bDeviceClass illisible pour %s — contrôle hub sauté "
                "(la borne realpath reste active)",
                device_dir,
            )
            return False

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
        dev_id = resolve_usb_device_id(device_path)
        if dev_id is None:
            return None
        path = f"{USB_DEVICES_ROOT}/{dev_id}/authorized"
        self._files[dev_id] = path
        self._state[path] = authorized
        return path

    def set_authorized(self, authorized_file: str, value: bool) -> None:
        self._state[authorized_file] = value

    async def resolve_authorized_file(self, device_path: str | None) -> str | None:
        dev_id = resolve_usb_device_id(device_path)
        if dev_id is None:
            return None
        return self._files.get(dev_id)

    async def read_authorized(self, authorized_file: str) -> bool:
        return self._state.get(authorized_file, False)

    async def write_authorized(self, authorized_file: str, authorized: bool) -> None:
        self.writes.append((authorized_file, authorized))
        if authorized_file in self._state:
            self._state[authorized_file] = authorized
        if authorized and self.on_reenumeration is not None:
            self.on_reenumeration()
