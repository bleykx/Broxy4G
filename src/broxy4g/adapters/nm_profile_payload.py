"""Payload GVariant du profil RNDIS smartphone + schéma de vérification (pur, aucune commande).

Faites mesurés sur hôte réel (démon NM 1.46, client nmcli/gdbus 1.52) —
ne « simplifiez » pas la forme :

- ``mac-address`` appartient au setting **``802-3-ethernet``**, PAS au
  setting ``connection`` : NM rejette ``connection.mac-address`` avec
  ``GDBus.Error:…InvalidProperty: connection.mac-address: unknown property``.
- Son type D-Bus est **``ay``** (tableau d'octets), pas une chaîne :
  ``{'mac-address': <'aa:bb:…'>}`` → ``can't set property of type 'ay'
  from value of type 's'``.
- Dans une variante, il faut à la fois ``<…>`` et des octets explicites :
  ``@ay [byte …]`` ou ``b'…'`` sans ``<…>`` → erreur de parsing GVariant
  (``can not parse as value of type 'v'``).
- ``<b'\\x02…'>`` est accepté comme ``ay`` mais les échappements passent en
  ASCII (NM a lu ``78:30:32…`` = la chaîne ``"x02x36…"``) : ne pas utiliser.
- Forme acceptée (profil créé, MAC relue, ``connection.interface-name`` vide
  → vraie liaison par MAC) :
  ``<[byte 0x02, byte 0x36, byte 0x0c, byte 0x7c, byte 0x14, byte 0x7a]>``
  (6 octets, hexadécimal minuscule à deux chiffres).
- Relecture : ``nmcli -g 802-3-ethernet.mac-address con show <p>`` rend
  ``02\\:36\\:0C\\:7C\\:14\\:7A`` — **majuscules** et ``:`` échappés d'un
  antislash par la sortie terse : normaliser (antislashs retirés +
  minuscules) avant comparaison, des deux côtés.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Champs re-lus par ``nmcli -g`` : le dernier est l'**identité** (MAC en mode
# MAC, nom d'interface en repli) ; les quatre premiers sont la politique.
VERIFY_FIELDS_MAC = (
    "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,"
    "connection.autoconnect,802-3-ethernet.mac-address"
)
VERIFY_FIELDS_IFACE = (
    "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,"
    "connection.autoconnect,connection.interface-name"
)

_MAC_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$", re.IGNORECASE)


@dataclass(frozen=True)
class ProfileBinding:
    """Profil à écrire : payload GVariant + schéma de sa vérification.

    ``expected_identity`` est le dernier champ attendu par
    :func:`check_profile_values` (déjà minuscules en mode MAC).
    """

    variant: str
    verify_fields: str
    expected_identity: str


def parse_mac_bytes(mac: str) -> list[int] | None:
    """MAC ``aa:bb:cc:dd:ee:ff`` → 6 octets (int) ; ``None`` sinon.

    N'importe quelle autre forme (hex invalide, longueur erronée, espaces)
    est rejetée : le profil ne serait pas une identité fiable.
    ``00:00:00:00:00:00`` reste accepté (identité légitime pour le bind).
    """
    if mac is None or _MAC_RE.fullmatch(mac) is None:
        return None
    return [int(part, 16) for part in mac.lower().split(":")]


def gquote(value: str) -> str:
    """Échappe défensivement une chaîne du dict GVariant (guillemets simples)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def build_profile_variant(name: str, interface: str, mac: str | None, metric: int) -> ProfileBinding:
    """Payload + schéma de vérification du profil RNDIS.

    Mode MAC (``mac`` convertible en 6 octets) : liaison par
    ``802-3-ethernet.mac-address`` (``ay``) ; le bloc ``connection`` ne porte
    ni ``mac-address`` ni ``interface-name``. Repli (``mac is None`` ou
    invalide) : liaison par ``connection.interface-name``, aucun setting
    ``802-3-ethernet``.
    """
    mac_bytes = parse_mac_bytes(mac) if mac is not None else None
    if mac_bytes is not None:
        data = ", ".join(f"byte 0x{byte:02x}" for byte in mac_bytes)
        variant = (
            f"{{'connection': {{'id': <'{gquote(name)}'>, 'type': <'802-3-ethernet'>, "
            f"'autoconnect': <true>}}, "
            f"'802-3-ethernet': {{'mac-address': <[{data}]>}}, "
            f"'ipv4': {{'method': <'auto'>, 'never-default': <true>, "
            f"'route-metric': <{metric}>}}, 'ipv6': {{'method': <'disabled'>}}}}"
        )
        return ProfileBinding(variant, VERIFY_FIELDS_MAC, mac.lower())
    variant = (
        f"{{'connection': {{'id': <'{gquote(name)}'>, 'type': <'802-3-ethernet'>, "
        f"'interface-name': <'{gquote(interface)}'>, 'autoconnect': <true>}}, "
        f"'ipv4': {{'method': <'auto'>, 'never-default': <true>, "
        f"'route-metric': <{metric}>}}, 'ipv6': {{'method': <'disabled'>}}}}"
    )
    return ProfileBinding(variant, VERIFY_FIELDS_IFACE, interface)


def normalize_identity(value: str) -> str:
    """Normalise une identité lue par ``nmcli -g`` (cf. docstring) :

    antislashs d'échappement retirés (``02\\:36\\:…``), puis minuscules.
    Une MAC ne contient jamais d'antislash ; idem pour un nom d'interface.
    """
    return value.replace("\\", "").lower()


def check_profile_values(values: list[str], expected: list[str]) -> tuple[bool, bool]:
    """Compare la sortie ``nmcli -g`` lue au schéma attendu (6 champs).

    Retourne ``(conforme, identite_positive_fausse)``. Le second ne vaut que
    lorsque le champ identité (le dernier) est **positif** (non vide) et
    différent de l'attendu après normalisation : défaut d'isolation concluant
    — le profil est activable sur la mauvaise interface, l'appelant doit
    supprimer le profil qu'il vient d'écrire. Tout autre écart (champs
    politique, nombre de lignes, identité vide) est non concluant : pas de
    suppression sur incident transitoire.
    """
    if len(values) != len(expected):
        return False, False
    if values[:-1] != expected[:-1]:
        return False, False
    if not values[-1]:
        return False, False  # identité non lue : non concluant, pas de destruction
    ok = normalize_identity(values[-1]) == normalize_identity(expected[-1])
    return ok, not ok
