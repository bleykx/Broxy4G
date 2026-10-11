"""Nommage de l'interface réseau d'un dongle, dérivé de son label.

Responsabilité unique : dériver un nom d'interface Linux depuis le label du
dongle et résoudre de façon déterministe les collisions, sans dépendance à
l'ordre USB ni au nom d'interface courant.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

MAX_INTERFACE_NAME_LEN = 15  # limite Linux : IFNAMSIZ - 1

_FALLBACK_NAME = "dongle"
_EMPTY_IDENTITY_SUFFIX = "0000"
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class NamedDongle:
    identity_key: str
    label: str


def _sanitize(value: str) -> str:
    """Minuscules, sans accents, suites non [a-z0-9] réduites à un '-'."""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM.sub("-", value.lower()).strip("-")


def derive_interface_name(label: str, identity_key: str) -> str:
    """Nom de base : label sanitisé, tronqué à 15, 'dongle' si vide."""
    return _sanitize(label)[:MAX_INTERFACE_NAME_LEN].rstrip("-") or _FALLBACK_NAME


def _unique_name(base: str, identity_key: str, used: set[str]) -> str:
    """Nom unique : base, sinon base tronquée + '-<4 derniers car. d'identité>'."""
    if base not in used:
        return base
    suffix = "-" + (_sanitize(identity_key)[-4:] or _EMPTY_IDENTITY_SUFFIX)
    for counter in range(1000):
        extra = str(counter) if counter else ""
        budget = MAX_INTERFACE_NAME_LEN - len(suffix) - len(extra)
        candidate = base[:budget].rstrip("-") + suffix + extra
        if candidate not in used:
            return candidate
    raise ValueError(f"impossible de trouver un nom unique pour {identity_key!r}")


def plan_interface_names(dongles: Iterable[NamedDongle]) -> dict[str, str]:
    """Plan déterministe identity_key -> nom d'interface, sans collision.

    Tri par identity_key : le premier (en tri) garde le nom de base, les
    suivants reçoivent le suffixe d'identité.
    """
    names: dict[str, str] = {}
    used: set[str] = set()
    for dongle in sorted(dongles, key=lambda d: d.identity_key):
        name = _unique_name(
            derive_interface_name(dongle.label, dongle.identity_key),
            dongle.identity_key,
            used,
        )
        used.add(name)
        names[dongle.identity_key] = name
    return names
