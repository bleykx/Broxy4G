"""Exécution du conteneur proxy : démarrage, arrêt, suppression, transitions.

Responsabilité unique (comme ``ProxyMaterializer`` pour la matérialisation) :
faire vivre le conteneur 3proxy d'un proxy — réparation des identifiants,
réécriture des fichiers, réseau partagé, transitions d'état persistées et
nettoyage complet à la suppression. Extrait de ``ProxyProvisioningService``
pour garder les modules courts.
"""

import logging
from uuid import UUID

from broxy4g.adapters.docker_compose import DockerComposeAdapter
from broxy4g.domain.proxy import InvalidProxyTransition, Proxy, ProxyState
from broxy4g.repositories.allowed_host_repository import AllowedHostRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.proxy_credential_repair import ensure_credentials
from broxy4g.services.proxy_files import ProxyFileWriter
from broxy4g.services.proxy_materializer import ProxyMaterializer
from broxy4g.services.proxy_network import ProxyNetworkService
from broxy4g.services.proxy_secret_box import ProxySecretBox

logger = logging.getLogger(__name__)


class ProxyRuntimeService:
    def __init__(
        self,
        proxy_repo: ProxyRepository,
        docker_compose: DockerComposeAdapter,
        files: ProxyFileWriter,
        materializer: ProxyMaterializer,
        allowed_host_repo: AllowedHostRepository,
        network: ProxyNetworkService | None = None,
        secret_box: ProxySecretBox | None = None,
    ):
        self._proxy_repo = proxy_repo
        self._docker_compose = docker_compose
        self._files = files
        self._materializer = materializer
        self._allowed_host_repo = allowed_host_repo
        self._network = network
        self._secret_box = secret_box

    def _to_state(self, proxy: Proxy, state: ProxyState) -> None:
        if proxy.state == state:
            return
        try:
            proxy.change_state(state)
        except InvalidProxyTransition:
            proxy.state = state
            proxy.touch()

    async def up(self, proxy: Proxy) -> None:
        """Active le proxy, (re)écrit ses fichiers puis démarre son conteneur.

        Le réseau Docker partagé est garanti exploitable (``ensure_ready``)
        **avant** le ``docker compose up`` : sans le pont, l'attachement du
        conteneur à l'IP statique échouerait.
        """
        proxy.enabled = True
        if ensure_credentials(proxy, self._secret_box):
            proxy.touch()
            await self._proxy_repo.save(proxy)
        await self._materializer.materialize(proxy)
        if proxy.state not in (ProxyState.RUNNING, ProxyState.STARTING):
            self._to_state(proxy, ProxyState.STARTING)
            await self._proxy_repo.save(proxy)
        if self._network is not None:
            await self._network.ensure_ready()
        try:
            await self._docker_compose.up(proxy.container_name, self._files.paths.compose_dir(proxy.id))
        except Exception:
            self._to_state(proxy, ProxyState.CRASHED)
            await self._proxy_repo.save(proxy)
            raise
        self._to_state(proxy, ProxyState.RUNNING)
        await self._proxy_repo.save(proxy)

    async def down(self, proxy: Proxy) -> None:
        """Arrête le conteneur sans changer l'état désiré persisté."""
        await self._docker_compose.down(proxy.container_name, self._files.paths.compose_dir(proxy.id))
        self._to_state(proxy, ProxyState.STOPPED)
        await self._proxy_repo.save(proxy)

    async def remove(self, proxy_id: UUID) -> None:
        """Supprime l'instance : conteneur, IP autorisées, fichiers et ligne DB."""
        proxy = await self._proxy_repo.get_by_id(proxy_id)
        if proxy is None:
            return
        if proxy.enabled:
            try:
                await self._docker_compose.remove(proxy.container_name, self._files.paths.compose_dir(proxy_id))
            except Exception:
                # L'échec Docker ne doit pas bloquer le nettoyage : fichiers
                # et ligne DB sont toujours supprimés (sinon la ligne reste
                # orpheline et viole UNIQUE(source_type, source_ref) au
                # prochain provision du même IMEI).
                logger.exception(
                    "Échec du remove Docker du proxy %s ; nettoyage poursuivi", proxy_id
                )
        await self._allowed_host_repo.remove_all_for_proxy(proxy_id)
        self._files.remove_proxy_dir(proxy_id)
        await self._proxy_repo.delete(proxy_id)
