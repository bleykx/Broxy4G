"""Règles de l'interface d'écoute (ingress) des proxies 3proxy.

Distincte de l'interface uplink sortante d'un proxy LOCAL : l'interface
d'écoute est l'interface de l'hôte sur laquelle le port du proxy est publié.
Seules les interfaces ethernet et wifi sont autorisées : jamais un dongle
(WWAN), ni loopback/docker/veth.
"""

ALLOWED_LISTEN_KINDS = frozenset({"ethernet", "wifi"})


class InvalidListenInterface(ValueError):
    """Interface d'écoute refusée (type non autorisé, inconnue ou sans IPv4)."""


def is_listen_kind_allowed(kind: str) -> bool:
    return kind in ALLOWED_LISTEN_KINDS
