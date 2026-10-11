"""Normalisation et fusion des sources autorisées (allow) d'un proxy.

Formats acceptés : adresse IPv4, CIDR IPv4 (`a.b.c.d/nn`, `strict=False`) et
plage IPv4 `A.B.C.D-W.X.Y.Z`. IPv4 uniquement pour l'instant : l'IPv6 est
explicitement rejeté.
"""

import ipaddress
import re
from collections.abc import Iterable

_RANGE_RE = re.compile(
    r"^(?P<start>\d{1,3}(?:\.\d{1,3}){3})-(?P<end>\d{1,3}(?:\.\d{1,3}){3})$"
)


def normalize_allowed_host(value: str) -> str:
    """Normalise une source autorisée (IPv4, CIDR IPv4 ou plage IPv4).

    Lève ValueError pour tout autre format (texte, IPv6, plage inversée,
    octets hors bornes).
    """
    candidate = value.strip()
    if not candidate:
        raise ValueError("Source autorisée vide")
    if ":" in candidate:
        raise ValueError(f"IPv6 non pris en charge : {value!r}")

    if "/" in candidate:
        network = ipaddress.ip_network(candidate, strict=False)
        if not isinstance(network, ipaddress.IPv4Network):
            raise ValueError(f"Seul le CIDR IPv4 est pris en charge : {value!r}")
        return str(network)

    match = _RANGE_RE.match(candidate)
    if match is not None:
        start = ipaddress.IPv4Address(match["start"])
        end = ipaddress.IPv4Address(match["end"])
        if start > end:
            raise ValueError(f"Plage IPv4 inversée : {value!r}")
        return f"{start}-{end}"

    try:
        return str(ipaddress.IPv4Address(candidate))
    except ValueError as exc:
        raise ValueError(f"Hôte autorisé invalide (IPv4 attendu) : {value!r}") from exc


def merge_allowed_sources(*groups: Iterable[str]) -> list[str]:
    """Fusionne des groupes de sources : normalise, déduplique en préservant l'ordre."""
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for value in group:
            normalized = normalize_allowed_host(value)
            if normalized not in seen:
                seen.add(normalized)
                merged.append(normalized)
    return merged
