"""Résolution d'identifiant de périphérique USB depuis un chemin sysfs.

Source unique « chemin ``device`` rapporté par ModemManager → identifiant de
device USB » : le composant de chemin **le plus profond** qui est un
identifiant sysfs de device USB (``^\d+-[\d.]+$``), avec le suffixe
d'interface USB (``:N.M``) retiré pour remonter au device.

Les topologies réelles sont souvent **imbriquées** (Raspberry Pi) : le dongle
est derrière des hubs, p. ex.
``…/3f980000.usb/usb1/1-1/1-1.2/1-1.2.4`` → l'identifiant est ``1-1.2.4``
(le dongle), jamais ``1-1`` (le hub). Cibler un hub le désautoriserait avec
tous les périphériques en aval. Il n'y a jamais de repli vers un composant
parent.

Ce module ne fait aucun accès au système de fichiers : il ne résout que la
chaîne ; la vérification d'existence et la borne de chemin restent du côté
des adaptateurs qui l'utilisent.
"""

from __future__ import annotations

import re

# Racine des devices USB de sysfs (liste de liens symboliques ``<id>``).
USB_DEVICES_ROOT = "/sys/bus/usb/devices"

# Identifiant sysfs de device USB : port-tiret, segments séparés par des
# points (ex. 1-1, 1-1.2.4). Jamais de ``/`` ni de ``..``.
_USB_DEVICE_ID_RE = re.compile(r"^\d+-[\d.]+$")

# Identifiant + suffixe d'interface USB (ex. 1-1.2.4:1.0) → on remonte au device.
_USB_INTERFACE_RE = re.compile(r"^(\d+-[\d.]+):\d+\.\d+$")


def resolve_usb_device_id(device_path: str | None) -> str | None:
    """Identifiant USB du device : **le composant le plus profond** du chemin.

    - Imbriquée (Pi) : ``…/usb1/1-1/1-1.2/1-1.2.4`` → ``1-1.2.4``.
    - Plate : ``…/usb1/1-1.2`` → ``1-1.2``.
    - Suffixe d'interface ignoré : ``…/1-1.2.4/1-1.2.4:1.0`` → ``1-1.2.4``.
    - Identifiant nu : ``1-1.2.4`` → ``1-1.2.4``.
    - ``None``, chaîne vide ou aucun identifiant exploitable → ``None``
      (jamais d'exception, jamais de repli hasardeux vers un parent/hub).
    """
    if not device_path:
        return None
    device_id: str | None = None
    for component in device_path.split("/"):
        if _USB_DEVICE_ID_RE.match(component):
            device_id = component
            continue
        m = _USB_INTERFACE_RE.match(component)
        if m:
            device_id = m.group(1)
    return device_id
