"""Parsing des sorties brutes ``nmcli`` (adresses IPv4, passerelle DHCP,
énumération de connexions).

Fonctions pures (aucun shell) : l'adaptateur NetworkManager les applique sur
les sorties de ``nmcli`` et les tests les pilotent via des sorties simulées.

Fait mesuré sur hôte réel : ``nmcli -g IP4.ADDRESS`` peut imprimer
**plusieurs adresses sur la même ligne** (``192.168.172.79/24 | 192.168.172.78/24``)
quand le device porte notre bail ET une adresse résiduelle d'un ancien profil.
L'ordre imprimé ne reflète PAS l'adresse source du noyau. Le parsing ne doit
JAMAIS lever d'exception : sortie vide, espaces variables, préfixe absent,
tokens parasites → ``None`` ou première adresse valide.

Fait mesuré (client nmcli 1.52.1 / démon NM 1.46.0) : ``nmcli -t`` sort des
**colonnes séparées par ``:``** (pas des tabulations), les champs vides sont
vides (``…:802-3-ethernet:::no``), et le champ ``IFNAME`` n'existe **pas** dans
les champs autorisés d'une énumération (``con show``) — le parser doit donc
scinder sur ``:`` non échappés, tolérer les champs vides, et ne jamais lever.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

_ROUTERS_RE = re.compile(r"\brouters\s*=\s*(\S+)")


@dataclass(frozen=True)
class NmIpv4:
    address: str
    prefix: int


@dataclass(frozen=True)
class NmConnectionDetail:
    """Détail d'une connexion NM (lecture par uuid) : interface, uuid, MAC."""

    interface_name: str
    uuid: str
    mac: str


def parse_ipv4_address(output: str) -> NmIpv4 | None:
    """Première adresse IPv4 valide d'une sortie ``nmcli -g IP4.ADDRESS``.

    Multi-adresses séparées par ``|`` (espaces variables tolérées) ; un token
    sans préfixe est accepté avec préfixe 0. Jamais d'exception : sortie
    vide, préfixe corrompu ou token parasite → ``None``.
    """
    for line in output.splitlines():
        for token in line.split("|"):
            address, _, prefix = token.strip().partition("/")
            address = address.strip()
            try:
                ipaddress.IPv4Address(address)
            except ValueError:
                continue
            prefix = prefix.strip()
            if prefix and not prefix.isdigit():
                continue
            return NmIpv4(address=address, prefix=int(prefix) if prefix else 0)
    return None


def parse_dhcp_gateway(output: str) -> str | None:
    """Première passerelle ``routers = <ip>`` d'une sortie ``nmcli -f DHCP4``."""
    for line in output.splitlines():
        match = _ROUTERS_RE.search(line)
        if match:
            return match.group(1)
    return None


def split_nmcli_t(line: str) -> list[str]:
    """Champ(s) d'une ligne ``nmcli -t -f`` : séparateur ``:``, échappement ``\\:``.

    Fait mesuré : ``nmcli -t`` scinde les colonnes par ``:`` (jamais par
    tabulation) et les champs vides sont vides (``…:802-3-ethernet:::no``).
    Un ``:`` échappé (``\\:``) appartient au champ (un nom peut contenir
    ``:``). Jamais d'exception : ligne vide → ``[""]``, ``\\`` final → conservé.
    """
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def parse_connection_detail(output: str) -> NmConnectionDetail:
    """Détail d'une connexion : ``nmcli -g connection.interface-name,connection.
    uuid,802-3-ethernet.mac-address`` (une valeur par ligne, ordre fixe).

    Champs vides tolérés, lignes manquantes → ``""``. Jamais d'exception.
    """
    lines = [line.strip() for line in output.splitlines()]

    def at(index: int) -> str:
        return lines[index] if index < len(lines) else ""

    return NmConnectionDetail(interface_name=at(0), uuid=at(1), mac=at(2))


def parse_active_uuids(output: str) -> set[str]:
    """UUID des connexions actives de ``nmcli -t -f UUID,DEVICE con show --active``.

    Premier champ de chaque ligne (``:``-séparé) ; les lignes vides sont ignorées.
    Jamais d'exception.
    """
    active: set[str] = set()
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = split_nmcli_t(line)
        if fields and fields[0]:
            active.add(fields[0])
    return active
