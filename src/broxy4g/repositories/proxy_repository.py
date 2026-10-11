from uuid import UUID

from sqlalchemy.orm import sessionmaker

from broxy4g.domain.proxy import Proxy, ProxySource, ProxyState

from .database import ProxyORM


class ProxyRepository:
    def __init__(self, session_factory: sessionmaker):
        self.session_factory = session_factory

    def _proxy_to_orm(self, proxy: Proxy) -> ProxyORM:
        return ProxyORM(
            id=proxy.id,
            dongle_id=proxy.dongle_id,
            container_name=proxy.container_name,
            listen_port=proxy.listen_port,
            state=proxy.state.value,
            auth_user=proxy.auth_user,
            password_hash=proxy.password_hash,
            created_at=proxy.created_at,
            updated_at=proxy.updated_at,
            source_type=proxy.source_type.value,
            source_ref=proxy.source_ref,
            interface_name=proxy.interface_name,
            enabled=proxy.enabled,
            config_path=proxy.config_path,
            compose_path=proxy.compose_path,
            network_ip=proxy.network_ip,
            route_table_id=proxy.route_table_id,
            fwmark=proxy.fwmark,
        )

    def _proxy_from_orm(self, orm: ProxyORM) -> Proxy:
        return Proxy(
            id=orm.id,
            dongle_id=orm.dongle_id,
            container_name=orm.container_name,
            listen_port=orm.listen_port,
            state=ProxyState(orm.state),
            auth_user=orm.auth_user,
            password_hash=orm.password_hash,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
            source_type=ProxySource(orm.source_type or "dongle"),
            source_ref=orm.source_ref,
            interface_name=orm.interface_name,
            enabled=bool(orm.enabled),
            config_path=orm.config_path,
            compose_path=orm.compose_path,
            network_ip=orm.network_ip,
            route_table_id=orm.route_table_id,
            fwmark=orm.fwmark,
        )

    def _apply_to_orm(self, orm: ProxyORM, proxy: Proxy) -> None:
        orm.dongle_id = proxy.dongle_id
        orm.container_name = proxy.container_name
        orm.listen_port = proxy.listen_port
        orm.state = proxy.state.value
        orm.auth_user = proxy.auth_user
        orm.password_hash = proxy.password_hash
        orm.updated_at = proxy.updated_at
        orm.source_type = proxy.source_type.value
        orm.source_ref = proxy.source_ref
        orm.interface_name = proxy.interface_name
        orm.enabled = proxy.enabled
        orm.config_path = proxy.config_path
        orm.compose_path = proxy.compose_path
        orm.network_ip = proxy.network_ip
        orm.route_table_id = proxy.route_table_id
        orm.fwmark = proxy.fwmark

    async def save(self, proxy: Proxy) -> Proxy:
        with self.session_factory() as session:
            existing = session.query(ProxyORM).filter_by(id=proxy.id).first()
            if existing:
                self._apply_to_orm(existing, proxy)
                session.commit()
                return self._proxy_from_orm(existing)
            orm = self._proxy_to_orm(proxy)
            session.add(orm)
            session.commit()
            return self._proxy_from_orm(orm)

    async def get_by_id(self, proxy_id: UUID) -> Proxy | None:
        with self.session_factory() as session:
            orm = session.query(ProxyORM).filter_by(id=proxy_id).first()
            return self._proxy_from_orm(orm) if orm else None

    async def get_by_dongle_id(self, dongle_id: UUID) -> Proxy | None:
        with self.session_factory() as session:
            orm = session.query(ProxyORM).filter_by(dongle_id=dongle_id).first()
            return self._proxy_from_orm(orm) if orm else None

    async def get_by_source(self, source_type: ProxySource, source_ref: str) -> Proxy | None:
        with self.session_factory() as session:
            orm = (
                session.query(ProxyORM)
                .filter_by(source_type=source_type.value, source_ref=source_ref)
                .first()
            )
            return self._proxy_from_orm(orm) if orm else None

    async def list_all(self) -> list[Proxy]:
        with self.session_factory() as session:
            orms = session.query(ProxyORM).order_by(ProxyORM.listen_port).all()
            return [self._proxy_from_orm(orm) for orm in orms]

    async def used_ports(self) -> set[int]:
        with self.session_factory() as session:
            rows = session.query(ProxyORM.listen_port).all()
            return {port for (port,) in rows if port is not None}

    async def list_by_dongle(self, dongle_id: UUID) -> list[Proxy]:
        with self.session_factory() as session:
            orms = session.query(ProxyORM).filter_by(dongle_id=dongle_id).all()
            return [self._proxy_from_orm(orm) for orm in orms]

    async def delete(self, proxy_id: UUID) -> bool:
        with self.session_factory() as session:
            deleted = session.query(ProxyORM).filter_by(id=proxy_id).delete()
            session.commit()
            return bool(deleted)
