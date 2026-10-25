"""Contrat runtime entre le service de pool et le gateway (aucun asyncio).

Le mot de passe du pool est déchiffré une fois par synchronisation et ne quitte
jamais la mémoire : il est porté par :class:`PoolRuntime` pour le seul passage
service → gateway, et ne doit ni être loggué, ni être persisté par le gateway.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class PoolRuntime:
    """Un pool à écouter par le gateway (identifiants en mémoire uniquement)."""

    pool_id: UUID
    label: str
    listen_port: int
    username: str
    password: str  # clair, transitoire : jamais loggué


@dataclass(frozen=True)
class PoolListenStatus:
    """Statut d'écoute d'un pool (jamais d'identifiant dedans)."""

    listening: bool
    bind_ip: str | None = None
    error: str | None = None
