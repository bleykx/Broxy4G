from sqlalchemy.orm import sessionmaker

from .database import SettingORM

AUTO_CREATE_PROXY_KEY = "proxy.auto_create"
LISTEN_INTERFACE_KEY = "proxy.listen_interface"

_TRUTHY = {"1", "true", "yes", "on"}


class SettingsRepository:
    def __init__(self, session_factory: sessionmaker):
        self.session_factory = session_factory

    async def get(self, key: str, default: str | None = None) -> str | None:
        with self.session_factory() as session:
            orm = session.query(SettingORM).filter_by(key=key).first()
            return orm.value if orm else default

    async def set(self, key: str, value: str) -> None:
        with self.session_factory() as session:
            orm = session.query(SettingORM).filter_by(key=key).first()
            if orm:
                orm.value = value
            else:
                session.add(SettingORM(key=key, value=value))
            session.commit()

    async def get_bool(self, key: str, default: bool) -> bool:
        raw = await self.get(key)
        if raw is None:
            return default
        return raw.strip().lower() in _TRUTHY

    async def set_bool(self, key: str, value: bool) -> None:
        await self.set(key, "1" if value else "0")
