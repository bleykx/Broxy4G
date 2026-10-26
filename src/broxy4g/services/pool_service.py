"""Service d'orchestration des pools de proxies.

Cas d'usage : création, mise à jour, suppression, membres, identifiants,
statut et synchronisation de l'écoute gateway. Le mot de passe clair ne quitte
jamais ce service que pour deux passages transitoires : le retour « une fois »
à la création et le ``PoolRuntime`` transmis au gateway. Il n'est jamais
journalisé : ni ``PoolRuntime``, ni ``PoolCandidate``, ni tuple d'identifiants.
"""

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from broxy4g.domain.pool_listen import PoolListenStatus, PoolRuntime
from broxy4g.domain.proxy_credentials import generate_credentials
from broxy4g.domain.proxy_pool import ProxyPool
from broxy4g.repositories.pool_repository import PoolRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.pool_gateway_port import PoolGatewayPort
from broxy4g.services.pool_membership import MemberEvaluation, PoolMembershipService
from broxy4g.services.pool_ports import PoolPortAllocator
from broxy4g.services.proxy_listen import ProxyListenService
from broxy4g.services.proxy_secret_box import ProxySecretBox

logger = logging.getLogger(__name__)


class PoolNotFound(Exception):
    """Pool inconnu (supprimé ou id invalide)."""


class PoolCredentialsUnavailable(Exception):
    """Secret du pool absent ou non déchiffrable (clé rotée, valeur corrompue)."""


class PoolMemberInvalid(Exception):
    """Proxy membre inconnu ou id invalide."""


@dataclass(frozen=True)
class PoolStatus:
    """Vue agrégée d'un pool : écoute, membres évalués, IP à communiquer."""

    pool: ProxyPool
    listen: PoolListenStatus
    members: list[MemberEvaluation]
    published_ip: str | None

    @property
    def endpoint(self) -> str | None:
        """``ip:port`` à communiquer aux clients ; ``None`` si le port n'est pas publié."""
        return f"{self.published_ip}:{self.pool.listen_port}" if self.published_ip else None


class PoolService:
    """Orchestre les pools au-dessus des proxies existants (aucune I/O système directe)."""

    def __init__(
        self,
        pool_repo: PoolRepository,
        proxy_repo: ProxyRepository,
        ports: PoolPortAllocator,
        membership: PoolMembershipService,
        secret_box: ProxySecretBox,
        gateway: PoolGatewayPort,
        listen: ProxyListenService,
    ):
        self._pool_repo = pool_repo
        self._proxy_repo = proxy_repo
        self._ports = ports
        self._membership = membership
        self._secret_box = secret_box
        self._gateway = gateway
        self._listen = listen
        # Allocation + persistance du port sous un même verrou : deux créations
        # simultanées ne peuvent pas se voir attribuer le même port.
        self._create_lock = asyncio.Lock()

    @property
    def gateway(self) -> PoolGatewayPort:
        """Gateway injecté (exposé pour les tests et le shutdown de main)."""
        return self._gateway

    async def create(self, label: str) -> tuple[ProxyPool, str]:
        """Crée un pool ; renvoie le pool et le mot de passe clair (une seule fois).

        Fail-closed : si le chiffrement du secret échoue, aucune ligne n'est
        écrite et :class:`PoolCredentialsUnavailable` est levée.
        """
        label = label.strip()
        if not label:
            raise ValueError("label requis")
        async with self._create_lock:
            port = await self._ports.allocate()
            credentials = generate_credentials()
            encrypted = self._secret_box.encrypt(credentials.password)
            if encrypted is None:
                raise PoolCredentialsUnavailable("coffre de secret indisponible : pool non créé")
            pool = await self._pool_repo.save(
                ProxyPool(
                    label=label,
                    listen_port=port,
                    auth_user=credentials.username,
                    password_encrypted=encrypted,
                )
            )
        logger.info("Pool %s créé (port %s, label %s)", pool.id, port, label)
        await self.sync_gateway()
        return pool, credentials.password

    async def get(self, pool_id: UUID) -> ProxyPool:
        """Charge un pool ; :class:`PoolNotFound` s'il n'existe pas."""
        pool = await self._pool_repo.get(pool_id)
        if pool is None:
            raise PoolNotFound("pool inconnu")
        return pool

    async def list_all(self) -> list[ProxyPool]:
        """Tous les pools, dans l'ordre stable du dépôt (port croissant)."""
        return await self._pool_repo.list_all()

    async def update(self, pool_id: UUID, *, label: str | None = None, enabled: bool | None = None) -> ProxyPool:
        """Met à jour ``label`` et/ou ``enabled`` ; resynchronise l'écoute."""
        pool = await self.get(pool_id)
        if label is not None:
            label = label.strip()
            if not label:
                raise ValueError("label requis")
            pool.label = label
        if enabled is not None and enabled != pool.enabled:
            pool.enabled = enabled
            logger.info("Pool %s %s", pool_id, "activé" if enabled else "désactivé")
        pool.touch()
        await self._pool_repo.save(pool)
        await self.sync_gateway()
        return pool

    async def delete(self, pool_id: UUID) -> None:
        """Supprime le pool et ses membres ; :class:`PoolNotFound` s'il n'existait pas."""
        if not await self._pool_repo.delete(pool_id):
            raise PoolNotFound("pool inconnu")
        logger.info("Pool %s supprimé", pool_id)
        await self.sync_gateway()

    async def add_member(self, pool_id: UUID, proxy_id: UUID) -> bool:
        """Ajoute un proxy au pool (idempotent) ; ``False`` si déjà membre.

        Aucune synchronisation gateway : l'éligibilité est re-évaluée à chaque
        connexion, donc l'ajout n'a pas d'effet immédiat sur l'écoute.
        """
        await self.get(pool_id)
        proxy = await self._proxy_repo.get_by_id(proxy_id)
        if proxy is None:
            raise PoolMemberInvalid("proxy inconnu")
        added = await self._pool_repo.add_member(pool_id, proxy_id)
        if added:
            logger.info("Proxy %s ajouté au pool %s", proxy_id, pool_id)
        return added

    async def remove_member(self, pool_id: UUID, proxy_id: UUID) -> bool:
        """Retire un proxy du pool ; ``False`` s'il n'était pas membre."""
        await self.get(pool_id)
        removed = await self._pool_repo.remove_member(pool_id, proxy_id)
        if removed:
            logger.info("Proxy %s retiré du pool %s", proxy_id, pool_id)
        return removed

    async def credentials(self, pool_id: UUID) -> tuple[str, str]:
        """Identifiants du pool ; jamais journalisés ici (le router journalise l'accès)."""
        pool = await self.get(pool_id)
        password = self._secret_box.decrypt(pool.password_encrypted)
        if not pool.auth_user or password is None:
            raise PoolCredentialsUnavailable("identifiants du pool non récupérables")
        return pool.auth_user, password

    async def status(self, pool_id: UUID) -> PoolStatus:
        """Statut agrégé : membres évalués, écoute, IP publiée."""
        pool = await self.get(pool_id)
        members = await self._membership.evaluate(pool_id)
        listen = self._gateway.status(pool_id)
        published_ip = await self._listen.resolve_published_ip()
        return PoolStatus(pool=pool, listen=listen, members=members, published_ip=published_ip)

    async def list_statuses(self) -> list[PoolStatus]:
        """Statut de chaque pool, dans l'ordre de :meth:`list_all`."""
        return [await self.status(pool.id) for pool in await self.list_all()]

    async def available_labels(self, pool_id: UUID) -> list[str]:
        """Labels des membres éligibles, dans l'ordre stable des membres du pool.

        Liste vide si aucun membre n'est éligible (pool debout mais inutilisable).
        :class:`PoolNotFound` si le pool n'existe pas.
        """
        await self.get(pool_id)
        evaluations = await self._membership.evaluate(pool_id)
        return [
            evaluation.label
            for evaluation in evaluations
            if evaluation.eligible and evaluation.label
        ]

    async def sync_gateway(self) -> None:
        """Réaligne l'écoute sur les pools activés et identifiés (jamais d'exception).

        Un pool sans identifiants exploitables n'est pas exposé (un pool actif
        ne doit jamais être un proxy ouvert). Appelée au boot, par le worker et
        par l'API : elle absorbe toute erreur pour ne jamais casser ses
        appelants.
        """
        try:
            runtimes: list[PoolRuntime] = []
            for pool in await self.list_all():
                if not pool.enabled:
                    continue
                password = self._secret_box.decrypt(pool.password_encrypted)
                if not pool.auth_user or password is None:
                    logger.warning("Pool %s sans identifiants exploitables : non exposé", pool.id)
                    continue
                runtimes.append(
                    PoolRuntime(
                        pool_id=pool.id,
                        label=pool.label,
                        listen_port=pool.listen_port,
                        username=pool.auth_user,
                        password=password,
                    )
                )
            await self._gateway.sync(runtimes)
        except Exception:
            logger.exception("Synchronisation du gateway de pools impossible")

    async def prune_members(self) -> int:
        """Supprime les membres dont le proxy n'existe plus ; jamais d'exception."""
        try:
            valid = {proxy.id for proxy in await self._proxy_repo.list_all()}
            removed = await self._pool_repo.prune_members(valid)
            if removed:
                logger.info("%s membre(s) de pool orphelin(s) supprimé(s)", removed)
            return removed
        except Exception:
            logger.exception("Nettoyage des membres de pools impossible")
            return 0
