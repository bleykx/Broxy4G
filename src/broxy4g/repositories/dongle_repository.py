from uuid import UUID

from sqlalchemy.orm import sessionmaker

from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial

from .database import DongleORM


class DongleRepository:
    def __init__(self, session_factory: sessionmaker):
        self.session_factory = session_factory

    def _dongle_to_orm(self, dongle: Dongle) -> DongleORM:
        return DongleORM(
            id=dongle.id,
            identity_imei=dongle.identity.imei,
            identity_usb_serial=dongle.identity.usb_serial,
            identity_iccid=dongle.identity.iccid,
            identity_key=dongle.identity_key,
            label=dongle.label,
            state=dongle.state.value,
            apn=dongle.apn,
            apn_auth=dongle.apn_auth,
            route_table_id=dongle.route_table_id,
            fwmark=dongle.fwmark,
            interface_name=dongle.interface_name,
            proxy_id=dongle.proxy_id,
            last_error=dongle.last_error,
            created_at=dongle.created_at,
            updated_at=dongle.updated_at,
        )

    def _dongle_from_orm(self, orm: DongleORM) -> Dongle:
        return Dongle(
            identity=IdentityMaterial(
                imei=orm.identity_imei,
                usb_serial=orm.identity_usb_serial,
                iccid=orm.identity_iccid,
            ),
            label=orm.label,
            state=DongleState(orm.state),
            id=orm.id,
            apn=orm.apn,
            apn_auth=orm.apn_auth,
            route_table_id=orm.route_table_id,
            fwmark=orm.fwmark,
            interface_name=orm.interface_name,
            proxy_id=orm.proxy_id,
            last_error=orm.last_error,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )

    async def save(self, dongle: Dongle) -> Dongle:
        with self.session_factory() as session:
            existing = session.query(DongleORM).filter_by(identity_key=dongle.identity_key).first()
            if existing:
                existing.identity_imei = dongle.identity.imei
                existing.identity_usb_serial = dongle.identity.usb_serial
                existing.identity_iccid = dongle.identity.iccid
                existing.label = dongle.label
                existing.state = dongle.state.value
                existing.apn = dongle.apn
                existing.apn_auth = dongle.apn_auth
                existing.route_table_id = dongle.route_table_id
                existing.fwmark = dongle.fwmark
                existing.interface_name = dongle.interface_name
                existing.proxy_id = dongle.proxy_id
                existing.last_error = dongle.last_error
                existing.updated_at = dongle.updated_at
            else:
                orm = self._dongle_to_orm(dongle)
                session.add(orm)
            session.commit()
            if existing:
                return self._dongle_from_orm(existing)
            return self._dongle_from_orm(orm)

    async def get_by_id(self, dongle_id: UUID) -> Dongle | None:
        with self.session_factory() as session:
            orm = session.query(DongleORM).filter_by(id=dongle_id).first()
            return self._dongle_from_orm(orm) if orm else None

    async def get_by_identity_key(self, identity_key: str) -> Dongle | None:
        with self.session_factory() as session:
            orm = session.query(DongleORM).filter_by(identity_key=identity_key).first()
            return self._dongle_from_orm(orm) if orm else None

    async def list_all(self) -> list[Dongle]:
        with self.session_factory() as session:
            orms = session.query(DongleORM).all()
            return [self._dongle_from_orm(orm) for orm in orms]

    async def delete(self, dongle_id: UUID) -> bool:
        with self.session_factory() as session:
            deleted = session.query(DongleORM).filter_by(id=dongle_id).delete()
            session.commit()
            return bool(deleted)
