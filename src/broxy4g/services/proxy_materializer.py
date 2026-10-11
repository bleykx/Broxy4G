"""Matérialisation d'un proxy 3proxy : sources, cfg, compose et publication.

Rend et écrit les fichiers de l'instance (cfg 3proxy + compose Docker) en
résolvant l'interface d'écoute courante, et re-publie un proxy déjà lancé quand
son compose désiré diverge du disque. Séparé du cycle de vie (création, up/down,
suppression) pour garder chaque module court.
"""

import logging
import os
from uuid import UUID

from broxy4g.adapters.docker_compose import DockerComposeAdapter
from broxy4g.domain.allowed_hosts import merge_allowed_sources
from broxy4g.domain.proxy import Proxy
from broxy4g.domain.proxy_compose import ComposeSpec, render_docker_compose
from broxy4g.domain.proxy_config import ProxyConfigSpec, render_3proxy_config
from broxy4g.domain.proxy_network import DOCKER_GATEWAY
from broxy4g.repositories.allowed_host_repository import AllowedHostRepository
from broxy4g.services.proxy_files import ProxyFileWriter
from broxy4g.services.proxy_listen import ProxyListenService

logger = logging.getLogger(__name__)

HOST_SOURCES_ENV = "BROXY4G_PROXY_HOST_IPS"
DEFAULT_HOST_SOURCES = ("127.0.0.1", DOCKER_GATEWAY)


def default_host_sources() -> list[str]:
    """IP de l'hôte autorisées par défaut : loopback + passerelle Docker.

    Le conteneur voit le trafic du proxy web publié soit comme `127.0.0.1`
    (loopback préservé) soit comme l'adresse de la passerelle Docker ; les IP
    supplémentaires se configurent via `BROXY4G_PROXY_HOST_IPS`.
    """
    sources: list[str] = list(DEFAULT_HOST_SOURCES)
    extra = os.environ.get(HOST_SOURCES_ENV, "")
    sources.extend(item.strip() for item in extra.split(",") if item.strip())
    return sources


class ProxyMaterializer:
    def __init__(
        self,
        allowed_host_repo: AllowedHostRepository,
        files: ProxyFileWriter,
        docker_compose: DockerComposeAdapter,
        listen: ProxyListenService | None = None,
        host_sources: list[str] | None = None,
    ):
        self._allowed_host_repo = allowed_host_repo
        self._files = files
        self._docker_compose = docker_compose
        self._listen = listen
        self._host_sources = host_sources

    async def allowed_sources(self, proxy_id: UUID | None) -> list[str]:
        host = self._host_sources if self._host_sources is not None else default_host_sources()
        groups = [host, await self._allowed_host_repo.list_global()]
        if proxy_id is not None:
            groups.append(await self._allowed_host_repo.list_for_proxy(proxy_id))
        return merge_allowed_sources(*groups)

    async def _resolve_bind_ip(self) -> str | None:
        """IP de bind publiée ; `0.0.0.0` par défaut si aucun service d'écoute."""
        if self._listen is None:
            return "0.0.0.0"
        return await self._listen.resolve_bind_ip()

    async def render_compose(self, proxy: Proxy) -> str:
        """Rend le compose désiré (sans écrire) en tenant compte du bind courant."""
        return render_docker_compose(
            ComposeSpec(
                project_name=proxy.container_name,
                container_name=proxy.container_name,
                listen_port=proxy.listen_port,
                cfg_path=str(self._files.paths.config_path(proxy.id)),
                network_ip=proxy.network_ip or "",
                bind_ip=await self._resolve_bind_ip(),
            )
        )

    async def materialize(self, proxy: Proxy) -> None:
        """Rend et écrit le cfg 3proxy + le compose de l'instance, sans persister."""
        allowed = await self.allowed_sources(proxy.id)
        cfg = render_3proxy_config(
            ProxyConfigSpec(
                username=proxy.auth_user or "",
                password_hash=proxy.password_hash or "",
                allowed_sources=tuple(allowed),
            )
        )
        compose = await self.render_compose(proxy)
        proxy.config_path = str(self._files.write_config(proxy.id, cfg))
        proxy.compose_path = str(self._files.write_compose(proxy.id, compose))

    async def ensure_publish(self, proxy: Proxy) -> None:
        """Re-publie un proxy déjà lancé si son compose désiré diffère du disque.

        Corrige une dérive de publication (changement de bind, interface d'écoute
        revenue) sans redémarrer un conteneur dont le compose n'a pas bougé.
        """
        desired = await self.render_compose(proxy)
        compose_path = self._files.paths.compose_path(proxy.id)
        current = compose_path.read_text() if compose_path.exists() else None
        if current == desired:
            return
        await self.materialize(proxy)
        await self._docker_compose.up(proxy.container_name, self._files.paths.compose_dir(proxy.id))
