"""Modèle de domaine des pools de proxies.

Un pool regroupe plusieurs proxies et les expose derrière un port unique dédié
(plage ``31301-31399``, disjointe de la plage des proxies). Le pool est un
point d'entrée seulement : il ne crée aucune route et ne possède pas de machine
à états — ``enabled`` suffit (l'écoute est réalignée par la réconciliation).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

# Plage de ports dédiée aux pools, disjointe de la plage des proxies
# (``PROXY_PORT_START``/``PROXY_PORT_END``) et du port de l'API.
POOL_PORT_START = 31301
POOL_PORT_END = 31399


@dataclass(frozen=True)
class PoolMember:
    """Liaison (pool, proxy) ; un proxy peut appartenir à plusieurs pools."""

    pool_id: UUID
    proxy_id: UUID


@dataclass
class ProxyPool:
    """Pool de proxies exposé sur un port unique.

    ``password_encrypted`` contient uniquement le mot de passe chiffré au repos
    (Fernet) : le mot de passe clair n'est jamais persisté.
    """

    label: str
    listen_port: int
    id: UUID = field(default_factory=uuid4)
    auth_user: str | None = None
    password_encrypted: str | None = None
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)
