"""Santé du proxy 3proxy d'un dongle : exposition API.

Croise trois critères, dans l'ordre :
1. le proxy persisté existe et est actif (``enabled``) ;
2. le conteneur Docker est réellement ``running`` ;
3. le port est publié (adresse publiée résolvable) ET joignable en TCP.

Le probe TCP (1 s, injectable via ``probe``) vise **l'adresse qu'un client du
LAN utiliserait pour joindre le port publié** (``resolve_published_ip`` :
interface d'écoute configurée, sinon première interface ethernet/wifi avec
IPv4, repli ``127.0.0.1``), et non un listener local de l'hôte : le port
publié est servi par DNAT, pas par un listener hôte. La sonde traverse donc
le chemin publié côté hôte : chaîne OUTPUT → DNAT → route → conteneur.

Limite assumée : partant de l'hôte, la sonde ne couvre pas un drop dans la
chaîne FORWARD (le parcours d'ingrès d'un paquet venant du LAN n'est pas
reproduit) ; sans namespace réseau dédié, ce n'est pas mesurable depuis
l'hôte. Ce n'est pas un défaut de cette sonde, mais un choix assumé
(voir ``docs/networking.md``).
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from broxy4g.adapters.docker_compose import DockerComposeAdapter
from broxy4g.domain.proxy import Proxy
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.proxy_listen import ProxyListenService

HEALTHY = "healthy"
UNHEALTHY = "unhealthy"
UNKNOWN = "unknown"

__all__ = ["HEALTHY", "UNHEALTHY", "UNKNOWN", "ProxyHealth", "ProxyHealthService"]


@dataclass(frozen=True)
class ProxyHealth:
    status: str
    reason: str | None = None


class ProxyHealthService:
    """Calcule la santé effective du proxy d'un dongle."""

    def __init__(
        self,
        proxy_repo: ProxyRepository,
        docker_compose: DockerComposeAdapter,
        listen: ProxyListenService | None = None,
        probe: Callable[[str, int], Awaitable[bool]] | None = None,
    ):
        self._proxy_repo = proxy_repo
        self._docker_compose = docker_compose
        self._listen = listen
        self._probe = probe

    async def for_dongle(self, dongle_id: UUID) -> ProxyHealth:
        proxy = await self._proxy_repo.get_by_dongle_id(dongle_id)
        if proxy is None:
            return ProxyHealth(UNKNOWN, "aucun proxy")
        return await self.for_proxy(proxy)

    async def for_proxy(self, proxy: Proxy) -> ProxyHealth:
        """Santé d'un proxy persisté quel qu'il soit (dongle ou uplink local)."""
        if not proxy.enabled:
            return ProxyHealth(UNKNOWN, "proxy désactivé")

        state = await self._docker_compose.state(proxy.container_name)
        if state != "running":
            return ProxyHealth(UNHEALTHY, f"conteneur non actif (state={state or 'absent'})")

        # Cible : l'adresse qu'un client du LAN utiliserait pour joindre le
        # port publié (jamais un listener local : le port est servi par DNAT).
        target = await self._listen.resolve_published_ip() if self._listen is not None else "127.0.0.1"
        if target is None:
            return ProxyHealth(UNHEALTHY, "port non publié : interface d'écoute absente ou sans IPv4")
        if not await self._reachable(target, proxy.listen_port):
            return ProxyHealth(UNHEALTHY, f"port publié non joignable depuis le LAN ({target}:{proxy.listen_port})")
        return ProxyHealth(HEALTHY, None)

    async def _reachable(self, target: str, port: int) -> bool:
        """Probe TCP sur l'adresse publiée, bornée à 1 s (127.0.0.1 si 0.0.0.0)."""
        if self._probe is not None:
            return await self._probe(target, port)
        host = "127.0.0.1" if target in ("0.0.0.0", "") else target
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1.0)
        except OSError:
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionError):
            pass
        return True
