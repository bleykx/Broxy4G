"""Contrat du gateway de pool, pour que le service soit testable sans socket.

Le service de pool ne parle qu'à cette interface : la synchronisation envoie
les ``PoolRuntime`` (identifiants clairs transitoires), le statut ne renvoie
jamais d'identifiant. :class:`FakePoolGateway` suit le même pattern que
``FakeDockerCompose`` pour les tests.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from broxy4g.domain.pool_listen import PoolListenStatus, PoolRuntime


@runtime_checkable
class PoolGatewayPort(Protocol):
    """Port gateway du service de pool (écoute et statut)."""

    async def sync(self, runtimes: Sequence[PoolRuntime]) -> None:
        """Réaligne l'écoute sur la liste de pools à exposer."""
        ...

    def status(self, pool_id: UUID) -> PoolListenStatus:
        """Statut d'écoute d'un pool (jamais d'identifiant dedans)."""
        ...

    async def stop_all(self) -> None:
        """Arrête toutes les écoutes de pool."""
        ...


class FakePoolGateway:
    """Gateway factice : mémorise les synchronisations pour les assertions."""

    def __init__(self):
        self.runtimes: list[PoolRuntime] = []
        self.calls: list[list[PoolRuntime]] = []

    async def sync(self, runtimes: Sequence[PoolRuntime]) -> None:
        self.calls.append(list(runtimes))
        self.runtimes = list(runtimes)

    def status(self, pool_id: UUID) -> PoolListenStatus:
        listening = any(runtime.pool_id == pool_id for runtime in self.runtimes)
        return PoolListenStatus(listening=listening, bind_ip="127.0.0.1")

    async def stop_all(self) -> None:
        self.runtimes = []
