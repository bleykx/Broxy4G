from uuid import UUID

from sqlalchemy.orm import sessionmaker

from broxy4g.domain.allowed_hosts import normalize_allowed_host

from .database import GlobalAllowedHostORM, ProxyAllowedHostORM


class AllowedHostRepository:
    def __init__(self, session_factory: sessionmaker):
        self.session_factory = session_factory

    async def list_global(self) -> list[str]:
        with self.session_factory() as session:
            rows = session.query(GlobalAllowedHostORM).order_by(GlobalAllowedHostORM.host).all()
            return [row.host for row in rows]

    async def add_global(self, host: str) -> None:
        normalized = normalize_allowed_host(host)
        with self.session_factory() as session:
            existing = session.query(GlobalAllowedHostORM).filter_by(host=normalized).first()
            if existing is None:
                session.add(GlobalAllowedHostORM(host=normalized))
            session.commit()

    async def remove_global(self, host: str) -> bool:
        normalized = normalize_allowed_host(host)
        with self.session_factory() as session:
            deleted = session.query(GlobalAllowedHostORM).filter_by(host=normalized).delete()
            session.commit()
            return bool(deleted)

    async def list_for_proxy(self, proxy_id: UUID) -> list[str]:
        with self.session_factory() as session:
            rows = (
                session.query(ProxyAllowedHostORM)
                .filter_by(proxy_id=proxy_id)
                .order_by(ProxyAllowedHostORM.host)
                .all()
            )
            return [row.host for row in rows]

    async def add_for_proxy(self, proxy_id: UUID, host: str) -> None:
        normalized = normalize_allowed_host(host)
        with self.session_factory() as session:
            existing = (
                session.query(ProxyAllowedHostORM)
                .filter_by(proxy_id=proxy_id, host=normalized)
                .first()
            )
            if existing is None:
                session.add(ProxyAllowedHostORM(proxy_id=proxy_id, host=normalized))
            session.commit()

    async def remove_for_proxy(self, proxy_id: UUID, host: str) -> bool:
        normalized = normalize_allowed_host(host)
        with self.session_factory() as session:
            deleted = (
                session.query(ProxyAllowedHostORM)
                .filter_by(proxy_id=proxy_id, host=normalized)
                .delete()
            )
            session.commit()
            return bool(deleted)

    async def remove_all_for_proxy(self, proxy_id: UUID) -> int:
        with self.session_factory() as session:
            deleted = session.query(ProxyAllowedHostORM).filter_by(proxy_id=proxy_id).delete()
            session.commit()
            return int(deleted)
