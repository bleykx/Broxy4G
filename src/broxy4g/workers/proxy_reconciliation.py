"""Réconciliation des proxies : conteneur 3proxy et épinglage local."""
import logging

from broxy4g.adapters.docker_compose import DockerComposeAdapter
from broxy4g.domain.proxy import ProxySource
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.local_connections import LocalConnectionsService
from broxy4g.services.proxy_provisioning import ProxyProvisioningService

logger = logging.getLogger(__name__)


class ProxyReconciler:
    """Réaligne chaque proxy sur son état désiré (activé/désactivé)."""

    def __init__(
        self,
        proxy_repo: ProxyRepository,
        dongle_repo: DongleRepository,
        provisioning: ProxyProvisioningService,
        docker_compose: DockerComposeAdapter,
        local_connections: LocalConnectionsService | None = None,
    ):
        self.proxy_repo = proxy_repo
        self.dongle_repo = dongle_repo
        self.provisioning = provisioning
        self.docker_compose = docker_compose
        self.local_connections = local_connections

    async def reconcile_proxies(self) -> None:
        """Réaligne chaque proxy sur son état désiré (activé/désactivé)."""
        for proxy in await self.proxy_repo.list_all():
            try:
                if await self._is_orphan(proxy):
                    logger.info(
                        "Proxy orphelin %s (dongle disparu), suppression", proxy.id
                    )
                    await self.provisioning.remove(proxy.id)
                    continue
                if proxy.source_type is ProxySource.LOCAL:
                    await self._reconcile_local_proxy(proxy)
                    continue
                await self.reconcile_proxy(proxy)
            except Exception:
                logger.exception("Reconciliation échouée pour proxy %s", proxy.id)

    async def restore_dongle_proxy(self, dongle) -> None:
        """Réaligne le proxy du dongle sur son état désiré."""
        proxy = await self.proxy_repo.get_by_dongle_id(dongle.id)
        if proxy is not None:
            await self.reconcile_proxy(proxy)

    async def reconcile_proxy(self, proxy) -> None:
        """Recrée/redémarre un proxy activé, arrête un proxy désactivé."""
        state = await self.docker_compose.state(proxy.container_name)
        if proxy.enabled:
            if state != "running":
                logger.info("Proxy %s non actif (state=%s), reprovisionnement", proxy.id, state)
                await self.provisioning.reprovision(proxy.id)
            else:
                # Déjà running : corrige une dérive de publication (bind changé,
                # interface d'écoute revenue) sans redémarrer un conteneur inchangé.
                await self.provisioning.ensure_publish(proxy)
        elif state == "running":
            logger.info("Proxy %s désactivé mais actif, arrêt", proxy.id)
            await self.provisioning.down(proxy)

    async def _is_orphan(self, proxy) -> bool:
        """Un proxy de dongle dont le dongle n'existe plus est orphelin."""
        if proxy.source_type is not ProxySource.DONGLE or proxy.dongle_id is None:
            return False
        return await self.dongle_repo.get_by_id(proxy.dongle_id) is None

    async def _reconcile_local_proxy(self, proxy) -> None:
        """Restaure l'épinglage d'un proxy local puis réaligne son conteneur."""
        if self.local_connections is not None and proxy.interface_name:
            try:
                await self.local_connections.restore(proxy.interface_name)
            except Exception:
                logger.exception("Échec restauration épingle locale %s", proxy.interface_name)
        await self.reconcile_proxy(proxy)
