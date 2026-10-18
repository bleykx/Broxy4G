"""Parsing des sorties brutes ``nmcli`` (adresses IPv4 et passerelle DHCP).

Fonctions pures (aucun shell) : l'adaptateur NetworkManager les applique sur
les sorties de ``nmcli`` et les tests les pilotent via des sorties simulées.

Fait mesuré sur hôte réel : ``nmcli -g IP4.ADDRESS`` peut imprimer
**plusieurs adresses sur la même ligne** (``192.168.172.79/24 | 192.168.172.78/24``)
quand le device porte notre bail ET une adresse résiduelle d'un ancien profil.
L'ordre imprimé ne reflète PAS l'adresse source du noyau. Le parsing ne doit
JAMAIS lever d'exception : sortie vide, espaces variables, préfixe absent,
tokens parasites → ``None`` ou première adresse valide.
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
