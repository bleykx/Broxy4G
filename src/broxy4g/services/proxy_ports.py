"""Allocation des ports d'écoute 3proxy (31201-31299)."""

import asyncio

from broxy4g.repositories.proxy_repository import ProxyRepository

PORT_RANGE_START = 31201
PORT_RANGE_END = 31299


class PortRangeExhausted(Exception):
    """Aucun port libre dans la plage allouable."""


def pick_free_port(
    used: set[int], *, start: int = PORT_RANGE_START, end: int = PORT_RANGE_END
) -> int:
    """Renvoie le plus petit port libre de [start, end]. Lève PortRangeExhausted sinon."""
    for port in range(start, end + 1):
        if port not in used:
            return port
    raise PortRangeExhausted(f"Aucun port libre entre {start} et {end}")


class PortAllocator:
    """Sérialise (lock) lecture des ports utilisés + choix du prochain port."""

    def __init__(self, proxy_repo: ProxyRepository):
        self._proxy_repo = proxy_repo
        self._lock = asyncio.Lock()

    async def allocate(self) -> int:
        async with self._lock:
            return pick_free_port(await self._proxy_repo.used_ports())
