from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from broxy4g.domain.proxy_pool import PoolMember, ProxyPool

from .database import ProxyPoolMemberORM, ProxyPoolORM


class PoolRepository:
    """Persistance des pools de proxies et de leurs membres."""

    def __init__(self, session_factory: sessionmaker):
        self.session_factory = session_factory

    def _to_orm(self, pool: ProxyPool) -> ProxyPoolORM:
        return ProxyPoolORM(
            id=pool.id,
            label=pool.label,
            listen_port=pool.listen_port,
            auth_user=pool.auth_user,
            password_encrypted=pool.password_encrypted,
            enabled=pool.enabled,
            created_at=pool.created_at,
            updated_at=pool.updated_at,
        )

    def _from_orm(self, orm: ProxyPoolORM) -> ProxyPool:
        return ProxyPool(
            id=orm.id,
            label=orm.label,
            listen_port=orm.listen_port,
            auth_user=orm.auth_user,
            password_encrypted=orm.password_encrypted,
            enabled=bool(orm.enabled),
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )

    def _apply(self, orm: ProxyPoolORM, pool: ProxyPool) -> None:
        orm.label = pool.label
        orm.listen_port = pool.listen_port
        orm.auth_user = pool.auth_user
        orm.password_encrypted = pool.password_encrypted
        orm.enabled = pool.enabled
        orm.updated_at = pool.updated_at

    async def save(self, pool: ProxyPool) -> ProxyPool:
        with self.session_factory() as session:
            existing = session.query(ProxyPoolORM).filter_by(id=pool.id).first()
            if existing:
                self._apply(existing, pool)
                session.commit()
                return self._from_orm(existing)
            orm = self._to_orm(pool)
            session.add(orm)
            session.commit()
            return self._from_orm(orm)

    async def get(self, pool_id: UUID) -> ProxyPool | None:
        with self.session_factory() as session:
            orm = session.query(ProxyPoolORM).filter_by(id=pool_id).first()
            return self._from_orm(orm) if orm else None

    async def list_all(self) -> list[ProxyPool]:
        with self.session_factory() as session:
            orms = session.query(ProxyPoolORM).order_by(ProxyPoolORM.listen_port).all()
            return [self._from_orm(orm) for orm in orms]

    async def delete(self, pool_id: UUID) -> bool:
        """Supprime les membres puis la ligne ; ``False`` si le pool n'existait pas."""
        with self.session_factory() as session:
            session.query(ProxyPoolMemberORM).filter_by(pool_id=pool_id).delete()
            deleted = session.query(ProxyPoolORM).filter_by(id=pool_id).delete()
            session.commit()
            return bool(deleted)

    async def used_ports(self) -> set[int]:
        with self.session_factory() as session:
            rows = session.query(ProxyPoolORM.listen_port).all()
            return {port for (port,) in rows if port is not None}

    async def add_member(self, pool_id: UUID, proxy_id: UUID) -> bool:
        """Idempotent : ``False`` si le couple existe déjà, pas d'exception."""
        with self.session_factory() as session:
            exists = (
                session.query(ProxyPoolMemberORM)
                .filter_by(pool_id=pool_id, proxy_id=proxy_id)
                .first()
            )
            if exists:
                return False
            session.add(
                ProxyPoolMemberORM(
                    pool_id=pool_id,
                    proxy_id=proxy_id,
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()
            return True

    async def remove_member(self, pool_id: UUID, proxy_id: UUID) -> bool:
        with self.session_factory() as session:
            deleted = (
                session.query(ProxyPoolMemberORM)
                .filter_by(pool_id=pool_id, proxy_id=proxy_id)
                .delete()
            )
            session.commit()
            return bool(deleted)

    async def list_member_ids(self, pool_id: UUID) -> list[UUID]:
        """Identifiants des proxies membres, stables : ``created_at`` puis ``proxy_id``."""
        with self.session_factory() as session:
            orms = (
                session.query(ProxyPoolMemberORM)
                .filter_by(pool_id=pool_id)
                .order_by(ProxyPoolMemberORM.created_at, ProxyPoolMemberORM.proxy_id)
                .all()
            )
            return [orm.proxy_id for orm in orms]

    async def list_all_members(self) -> list[PoolMember]:
        with self.session_factory() as session:
            orms = session.query(ProxyPoolMemberORM).all()
            return [PoolMember(pool_id=orm.pool_id, proxy_id=orm.proxy_id) for orm in orms]

    async def memberships_for_proxy(self, proxy_id: UUID) -> list[UUID]:
        """Identifiants des pools dont le proxy est membre."""
        with self.session_factory() as session:
            rows = (
                session.query(ProxyPoolMemberORM.pool_id)
                .filter_by(proxy_id=proxy_id)
                .all()
            )
            return [pool_id for (pool_id,) in rows]

    async def prune_members(self, valid_proxy_ids: set[UUID]) -> int:
        """Supprime les membres dont le proxy n'existe plus.

        Si ``valid_proxy_ids`` est vide, ne supprime rien : un état vide peut
        venir d'un chargement raté des proxies.
        """
        if not valid_proxy_ids:
            return 0
        with self.session_factory() as session:
            deleted = (
                session.query(ProxyPoolMemberORM)
                .filter(~ProxyPoolMemberORM.proxy_id.in_(valid_proxy_ids))
                .delete(synchronize_session=False)
            )
            session.commit()
            return int(deleted or 0)
