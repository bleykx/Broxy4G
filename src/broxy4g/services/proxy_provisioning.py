"""Provisioning d'un proxy 3proxy : port, identifiants, création, recréation.

Orchestre les briques domaine/adaptateurs/repositories pour créer ou recréer
l'instance d'un proxy, idempotent par source. La matérialisation (cfg +
compose) est déléguée à ``ProxyMaterializer`` et l'exécution du conteneur
(up/down/remove, transitions d'état) à ``ProxyRuntimeService``.
"""

import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from broxy4g.adapters.docker_compose import DockerComposeAdapter
from broxy4g.domain.proxy import Proxy, ProxySource, ProxyState
from broxy4g.domain.proxy_credentials import ProxyCredentials, generate_credentials
from broxy4g.domain.proxy_network import network_ip_for_port
from broxy4g.repositories.allowed_host_repository import AllowedHostRepository
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.proxy_credential_repair import ensure_credentials
from broxy4g.services.proxy_files import ProxyFileWriter
from broxy4g.services.proxy_listen import ProxyListenService
from broxy4g.services.proxy_materializer import ProxyMaterializer
from broxy4g.services.proxy_network import ProxyNetworkService
from broxy4g.services.proxy_ports import PortAllocator
from broxy4g.services.proxy_runtime import ProxyRuntimeService
from broxy4g.services.proxy_secret_box import ProxySecretBox

logger = logging.getLogger(__name__)


@dataclass
class ProvisionedProxy:
    proxy: Proxy
    credentials: ProxyCredentials | None = None  # non-None uniquement à la création


def container_name_for(proxy_id: UUID) -> str:
    return f"broxy4g-proxy-{proxy_id}"


class ProxyProvisioningService:
    def __init__(
        self,
        proxy_repo: ProxyRepository,
        dongle_repo: DongleRepository,
        allowed_host_repo: AllowedHostRepository,
        docker_compose: DockerComposeAdapter,
        port_allocator: PortAllocator,
        files: ProxyFileWriter,
        host_sources: list[str] | None = None,
        *,
        listen: ProxyListenService | None = None,
        network: ProxyNetworkService | None = None,
        secret_box: ProxySecretBox | None = None,
    ):
        self._proxy_repo = proxy_repo
        self._dongle_repo = dongle_repo
        self._port_allocator = port_allocator
        self._secret_box = secret_box
        self._materializer = ProxyMaterializer(
            allowed_host_repo, files, docker_compose, listen, host_sources
        )
        self._runtime = ProxyRuntimeService(
            proxy_repo,
            docker_compose,
            files,
            self._materializer,
            allowed_host_repo,
            network=network,
            secret_box=secret_box,
        )

    # --- matérialisation (déléguée à ProxyMaterializer) --------------------

    async def materialize(self, proxy: Proxy) -> None:
        """Rend et écrit le cfg 3proxy + le compose de l'instance, sans persister."""
        await self._materializer.materialize(proxy)

    async def ensure_publish(self, proxy: Proxy) -> None:
        """Re-publie un proxy lancé dont le compose désiré diffère du disque."""
        await self._materializer.ensure_publish(proxy)

    # --- exécution (déléguée à ProxyRuntimeService) ------------------------

    async def up(self, proxy: Proxy) -> None:
        """Active le proxy et démarre son conteneur (cf. ``ProxyRuntimeService``)."""
        await self._runtime.up(proxy)

    async def down(self, proxy: Proxy) -> None:
        """Arrête le conteneur sans changer l'état désiré persisté."""
        await self._runtime.down(proxy)

    async def remove(self, proxy_id: UUID) -> None:
        """Supprime l'instance (conteneur, IP autorisées, fichiers, ligne DB)."""
        await self._runtime.remove(proxy_id)

    # --- création ----------------------------------------------------------

    def _encrypt_secret(self, password: str) -> str | None:
        """Chiffre le mot de passe au repos (None si coffre absent/inopérant)."""
        if self._secret_box is None:
            return None
        return self._secret_box.encrypt(password)

    def _build_proxy(
        self,
        *,
        source_type: ProxySource,
        source_ref: str,
        dongle_id: UUID | None,
        interface_name: str | None,
        port: int,
        credentials: ProxyCredentials,
        network_ip: str,
        enabled: bool,
    ) -> Proxy:
        proxy_id = uuid4()
        return Proxy(
            id=proxy_id,
            dongle_id=dongle_id,
            container_name=container_name_for(proxy_id),
            listen_port=port,
            state=ProxyState.STOPPED,
            auth_user=credentials.username,
            password_hash=credentials.password_hash,
            password_encrypted=self._encrypt_secret(credentials.password),
            source_type=source_type,
            source_ref=source_ref,
            interface_name=interface_name,
            enabled=enabled,
            network_ip=network_ip,
        )

    async def provision_dongle(self, dongle_id: UUID, *, enabled: bool = True) -> ProvisionedProxy:
        existing = await self._proxy_repo.get_by_dongle_id(dongle_id)
        if existing is not None:
            return ProvisionedProxy(proxy=existing)
        dongle = await self._dongle_repo.get_by_id(dongle_id)
        if dongle is None:
            raise ValueError(f"Dongle inconnu : {dongle_id}")
        stale = await self._proxy_repo.get_by_source(ProxySource.DONGLE, dongle.identity_key)
        if stale is not None:
            logger.info(
                "Proxy orphelin %s (source_ref=%s) supprimé avant provision",
                stale.id,
                dongle.identity_key,
            )
            await self.remove(stale.id)
        return await self._provision(
            source_type=ProxySource.DONGLE,
            source_ref=dongle.identity_key,
            dongle_id=dongle_id,
            interface_name=None,
            enabled=enabled,
        )

    async def provision_local(self, interface_name: str, *, enabled: bool = True) -> ProvisionedProxy:
        existing = await self._proxy_repo.get_by_source(ProxySource.LOCAL, interface_name)
        if existing is not None:
            return ProvisionedProxy(proxy=existing)
        return await self._provision(
            source_type=ProxySource.LOCAL,
            source_ref=interface_name,
            dongle_id=None,
            interface_name=interface_name,
            enabled=enabled,
        )

    async def _provision(
        self,
        *,
        source_type: ProxySource,
        source_ref: str,
        dongle_id: UUID | None,
        interface_name: str | None,
        enabled: bool,
    ) -> ProvisionedProxy:
        port = await self._port_allocator.allocate()
        credentials = generate_credentials()
        proxy = self._build_proxy(
            source_type=source_type,
            source_ref=source_ref,
            dongle_id=dongle_id,
            interface_name=interface_name,
            port=port,
            credentials=credentials,
            network_ip=network_ip_for_port(port),
            enabled=enabled,
        )
        await self.materialize(proxy)
        await self._proxy_repo.save(proxy)
        if enabled:
            await self.up(proxy)
        return ProvisionedProxy(proxy=proxy, credentials=credentials)

    async def reprovision(self, proxy_id: UUID) -> Proxy:
        proxy = await self._require(proxy_id)
        ensure_credentials(proxy, self._secret_box)
        await self.materialize(proxy)
        proxy.touch()
        await self._proxy_repo.save(proxy)
        if proxy.enabled:
            await self.up(proxy)
        return proxy

    async def _require(self, proxy_id: UUID) -> Proxy:
        proxy = await self._proxy_repo.get_by_id(proxy_id)
        if proxy is None:
            raise ValueError(f"Proxy inconnu : {proxy_id}")
        return proxy
