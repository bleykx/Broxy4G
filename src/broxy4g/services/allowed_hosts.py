"""Service applicatif des sources IP autorisées (globales et par proxy)."""

import logging
from typing import Protocol
from uuid import UUID

from broxy4g.domain.allowed_hosts import normalize_allowed_host
from broxy4g.repositories.allowed_host_repository import AllowedHostRepository

logger = logging.getLogger(__name__)


class ProxyConfigApplier(Protocol):
    async def reapply(self, proxy_id: UUID) -> None: ...
    async def reapply_all_enabled(self) -> None: ...


class AllowedHostsService:
    def __init__(self, repo: AllowedHostRepository, applier: ProxyConfigApplier | None = None):
        self.repo = repo
        self._applier = applier

    async def list_global(self) -> list[str]:
        return await self.repo.list_global()

    async def add_global(self, host: str) -> str:
        normalized = normalize_allowed_host(host)
        await self.repo.add_global(normalized)
        await self._reapply_all()
        return normalized

    async def remove_global(self, host: str) -> bool:
        result = await self.repo.remove_global(normalize_allowed_host(host))
        await self._reapply_all()
        return result

    async def list_for_proxy(self, proxy_id: UUID) -> list[str]:
        return await self.repo.list_for_proxy(proxy_id)

    async def add_for_proxy(self, proxy_id: UUID, host: str) -> str:
        normalized = normalize_allowed_host(host)
        await self.repo.add_for_proxy(proxy_id, normalized)
        await self._reapply(proxy_id)
        return normalized

    async def remove_for_proxy(self, proxy_id: UUID, host: str) -> bool:
        result = await self.repo.remove_for_proxy(proxy_id, normalize_allowed_host(host))
        await self._reapply(proxy_id)
        return result

    async def _reapply_all(self) -> None:
        if self._applier is None:
            return
        try:
            await self._applier.reapply_all_enabled()
        except Exception:
            logger.exception("Échec de la réapplication des proxies après mutation globale")

    async def _reapply(self, proxy_id: UUID) -> None:
        if self._applier is None:
            return
        try:
            await self._applier.reapply(proxy_id)
        except Exception:
            logger.exception("Échec de la réapplication du proxy %s", proxy_id)
