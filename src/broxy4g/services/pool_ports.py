"""Allocation du port d'écoute d'un pool (plage dédiée ``31301-31399``).

Réutilise la logique commune de :mod:`broxy4g.services.proxy_ports`
(``pick_free_port``/``PortRangeExhausted``) : aucun doublon de fonction.
Toute logique Docker reste dans l'adaptateur ; ici on ne fait que restreindre
les ports publiés à la plage des pools (un port publié hors plage n'est pas
compté).
"""

import asyncio

from broxy4g.adapters.docker_compose import DockerComposeAdapter
from broxy4g.domain.proxy_pool import POOL_PORT_END, POOL_PORT_START
from broxy4g.repositories.pool_repository import PoolRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.proxy_ports import PortRangeExhausted, pick_free_port

__all__ = ["PoolPortAllocator", "PortRangeExhausted"]


class PoolPortAllocator:
    """Choisit le plus petit port libre de la plage des pools, sous verrou.

    « Utilisés » = ports persistés des pools + ports persistés des proxies +
    ports hôte publiés par Docker **dans la plage pools** (si l'adaptateur
    ``docker_compose`` est fourni).
    """

    def __init__(
        self,
        pool_repo: PoolRepository,
        proxy_repo: ProxyRepository,
        docker_compose: DockerComposeAdapter | None = None,
    ):
        self._pool_repo = pool_repo
        self._proxy_repo = proxy_repo
        self._docker_compose = docker_compose
        self._lock = asyncio.Lock()

    async def allocate(self) -> int:
        async with self._lock:
            used = await self._pool_repo.used_ports()
            used |= await self._proxy_repo.used_ports()
            if self._docker_compose is not None:
                published = await self._docker_compose.published_host_ports()
                used |= {p for p in published if POOL_PORT_START <= p <= POOL_PORT_END}
            return pick_free_port(used, start=POOL_PORT_START, end=POOL_PORT_END)
