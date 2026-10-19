"""Cycle de vie d'exécution d'un proxy : démarrer, arrêter, recréer, tourner.

Les identifiants sont générés une seule fois (au provisioning) puis seuls
leurs hash sont persistés ; la rotation régénère un couple et le renvoie en
clair une seule fois.
"""

import logging
from uuid import UUID

from broxy4g.adapters.routing import RoutingAdapter
from broxy4g.domain.proxy import Proxy, ProxySource
from broxy4g.domain.proxy_credentials import ProxyCredentials, generate_credentials
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.network_isolation import NetworkIsolationService
from broxy4g.services.proxy_provisioning import (
    ProvisionedProxy,
    ProxyProvisioningService,
)
from broxy4g.services.proxy_secret_box import ProxySecretBox

logger = logging.getLogger(__name__)


class CredentialsUnavailable(Exception):
    """L'identifiant chiffré est absent ou illisible : rotation nécessaire."""


class ProxyLifecycleService:
    def __init__(
        self,
        proxy_repo: ProxyRepository,
        provisioning: ProxyProvisioningService,
        isolation: NetworkIsolationService | None = None,
        routing: RoutingAdapter | None = None,
        secret_box: ProxySecretBox | None = None,
    ):
        self.proxy_repo = proxy_repo
        self.provisioning = provisioning
        self.isolation = isolation
        self.routing = routing
        self.secret_box = secret_box

    async def _apply_isolation(self) -> None:
        if self.isolation is not None:
            await self.isolation.apply()

    # --- création ----------------------------------------------------------

    async def create(self, dongle_id: UUID, *, enabled: bool = True) -> ProvisionedProxy:
        provisioned = await self.provisioning.provision_dongle(dongle_id, enabled=enabled)
        await self._apply_isolation()
        return provisioned

    async def create_local(self, interface_name: str, *, enabled: bool = True) -> ProvisionedProxy:
        provisioned = await self.provisioning.provision_local(interface_name, enabled=enabled)
        await self._apply_isolation()
        return provisioned

    # --- transitions -------------------------------------------------------

    async def start(self, proxy_id: UUID) -> Proxy:
        proxy = await self._load(proxy_id)
        await self.provisioning.up(proxy)
        await self._apply_isolation()
        return proxy

    async def stop(self, proxy_id: UUID) -> Proxy:
        proxy = await self._load(proxy_id)
        await self.provisioning.down(proxy)
        proxy.enabled = False
        await self.proxy_repo.save(proxy)
        await self._apply_isolation()
        return proxy

    async def pause(self, proxy_id: UUID) -> Proxy:
        """Arrête le conteneur SANS changer l'état désiré persisté.

        ``enabled`` est conservé : la réconciliation restaure le proxy une
        fois l'uplink de nouveau utilisable (cas d'un reset de dongle)."""
        proxy = await self._load(proxy_id)
        await self.provisioning.down(proxy)
        await self._apply_isolation()
        return proxy

    async def resume(self, proxy_id: UUID) -> Proxy:
        """Reprend un proxy mis en pause : recrée le conteneur et réactive
        l'état désiré (``enabled = True``)."""
        proxy = await self._load(proxy_id)
        await self.provisioning.up(proxy)
        await self._apply_isolation()
        return proxy

    async def restart(self, proxy_id: UUID) -> Proxy:
        proxy = await self._load(proxy_id)
        await self.provisioning.down(proxy)
        await self.provisioning.up(proxy)
        await self._apply_isolation()
        return proxy

    async def reprovision(self, proxy_id: UUID) -> Proxy:
        proxy = await self.provisioning.reprovision(proxy_id)
        await self._apply_isolation()
        return proxy

    def _encrypt_secret(self, password: str) -> str | None:
        if self.secret_box is None:
            return None
        return self.secret_box.encrypt(password)

    async def rotate_credentials(self, proxy_id: UUID) -> ProxyCredentials:
        proxy = await self._load(proxy_id)
        credentials = generate_credentials()
        proxy.auth_user = credentials.username
        proxy.password_hash = credentials.password_hash
        proxy.password_encrypted = self._encrypt_secret(credentials.password)
        await self.provisioning.materialize(proxy)
        proxy.touch()
        await self.proxy_repo.save(proxy)
        if proxy.enabled:
            await self.provisioning.down(proxy)
            await self.provisioning.up(proxy)
        await self._apply_isolation()
        return credentials

    async def reapply(self, proxy_id: UUID) -> None:
        """Réapplique la conf (dont les IP autorisées) sans réactiver un proxy désactivé."""
        proxy = await self._load(proxy_id)
        if proxy.enabled:
            # Le compose ne change pas, seul le cfg monté change : `up -d` seul
            # ne recrée pas le conteneur, d'où la paire down + up.
            await self.provisioning.down(proxy)
            await self.provisioning.up(proxy)
        else:
            await self.provisioning.materialize(proxy)
            proxy.touch()
            await self.proxy_repo.save(proxy)

    async def reapply_all_enabled(self) -> None:
        for proxy in await self.proxy_repo.list_all():
            if not proxy.enabled:
                continue
            try:
                await self.reapply(proxy.id)
            except Exception:
                logger.exception("Échec de réapplication du proxy %s", proxy.id)

    async def remove(self, proxy_id: UUID) -> None:
        proxy = await self.proxy_repo.get_by_id(proxy_id)
        if proxy is not None and proxy.source_type is ProxySource.LOCAL:
            await self._release_pinning(proxy)
        await self.provisioning.remove(proxy_id)
        await self._apply_isolation()

    async def _release_pinning(self, proxy: Proxy) -> None:
        # On retire la règle fwmark du proxy, mais la table dédiée garde un
        # plancher ``default`` (unreachable) : une table dédiée ne doit JAMAIS
        # se retrouver sans route ``default`` (sinon repli sur ``main`` = fuite).
        if self.routing is None or not proxy.route_table_id or not proxy.fwmark:
            return
        await self.routing.remove_rule(proxy.route_table_id, proxy.fwmark)
        await self.routing.add_barrier_route(proxy.route_table_id)

    # --- lecture -----------------------------------------------------------

    async def get_credentials(self, proxy_id: UUID) -> ProxyCredentials:
        """Identifiants d'un proxy en déchiffrant le secret persisté au repos.

        Lève ``ValueError`` si le proxy est inconnu et ``CredentialsUnavailable``
        si le mot de passe n'est pas récupérable (ligne antérieure au stockage
        chiffré, ou valeur corrompue/clé rotée) : l'utilisateur doit tourner.
        """
        proxy = await self._load(proxy_id)
        password = self.secret_box.decrypt(proxy.password_encrypted) if self.secret_box else None
        if not proxy.auth_user or password is None:
            raise CredentialsUnavailable(
                "identifiants non récupérables : proxy antérieur au stockage "
                "chiffré, régénérer les identifiants"
            )
        return ProxyCredentials(
            username=proxy.auth_user,
            password=password,
            password_hash=proxy.password_hash or "",
        )

    async def get(self, proxy_id: UUID) -> Proxy | None:
        return await self.proxy_repo.get_by_id(proxy_id)

    async def get_by_dongle_id(self, dongle_id: UUID) -> Proxy | None:
        return await self.proxy_repo.get_by_dongle_id(dongle_id)

    async def list_all(self) -> list[Proxy]:
        return await self.proxy_repo.list_all()

    async def _load(self, proxy_id: UUID) -> Proxy:
        proxy = await self.proxy_repo.get_by_id(proxy_id)
        if proxy is None:
            raise ValueError("Proxy not found")
        return proxy
