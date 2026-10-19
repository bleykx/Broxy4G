import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint, inspect, text, types
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

Base = declarative_base()


class UUIDType(types.TypeDecorator):
    """Type de données pour UUID."""
    impl = types.String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return UUID(value)


class DongleORM(Base):
    __tablename__ = "dongles"

    id: UUID = Column(UUIDType, primary_key=True)
    identity_imei: str | None = Column(String)
    identity_usb_serial: str | None = Column(String)
    identity_iccid: str | None = Column(String)
    identity_adb_serial: str | None = Column(String)
    identity_key: str = Column(String, unique=True, index=True)
    label: str = Column(String)
    state: str = Column(String)
    apn: str | None = Column(String)
    apn_auth: str | None = Column(String)
    route_table_id: int | None = Column(Integer)
    fwmark: int | None = Column(Integer)
    interface_name: str | None = Column(String)
    proxy_id: UUID | None = Column(UUIDType)
    last_error: str | None = Column(String)
    transport: str = Column(String, nullable=False, default="modem", server_default="modem")
    created_at: datetime = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: datetime = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ProxyORM(Base):
    __tablename__ = "proxies"
    __table_args__ = (UniqueConstraint("source_type", "source_ref", name="ux_proxies_source"),)

    id: UUID = Column(UUIDType, primary_key=True)
    dongle_id: UUID | None = Column(UUIDType, index=True, nullable=True)
    container_name: str = Column(String)
    listen_port: int = Column(Integer, unique=True)
    state: str = Column(String)
    auth_user: str | None = Column(String)
    password_hash: str | None = Column(String)
    password_encrypted: str | None = Column(String)
    created_at: datetime = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: datetime = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    source_type: str = Column(String, default="dongle")
    source_ref: str | None = Column(String)
    interface_name: str | None = Column(String)
    enabled: bool = Column(Boolean, default=True)
    config_path: str | None = Column(String)
    compose_path: str | None = Column(String)
    network_ip: str | None = Column(String)
    route_table_id: int | None = Column(Integer)
    fwmark: int | None = Column(Integer)


class GlobalAllowedHostORM(Base):
    __tablename__ = "global_allowed_hosts"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    host: str = Column(String, unique=True)
    created_at: datetime = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ProxyAllowedHostORM(Base):
    __tablename__ = "proxy_allowed_hosts"
    __table_args__ = (UniqueConstraint("proxy_id", "host", name="ux_proxy_allowed_hosts"),)

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    proxy_id: UUID = Column(UUIDType, index=True)
    host: str = Column(String)
    created_at: datetime = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SettingORM(Base):
    __tablename__ = "settings"

    key: str = Column(String, primary_key=True)
    value: str = Column(String)


def create_engine(url: str):
    from sqlalchemy import create_engine as _create_engine
    return _create_engine(url)


def create_session_factory(engine):
    return sessionmaker(bind=engine)


_PROXY_MIGRATED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("password_hash", "VARCHAR"),
    ("password_encrypted", "VARCHAR"),
    ("source_type", "VARCHAR DEFAULT 'dongle'"),
    ("source_ref", "VARCHAR"),
    ("interface_name", "VARCHAR"),
    ("enabled", "BOOLEAN DEFAULT 1"),
    ("config_path", "VARCHAR"),
    ("compose_path", "VARCHAR"),
    ("network_ip", "VARCHAR"),
    ("route_table_id", "INTEGER"),
    ("fwmark", "INTEGER"),
)

_PROXY_MIGRATED_INDEXES: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_proxies_listen_port ON proxies(listen_port)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_proxies_source ON proxies(source_type, source_ref)",
)


def _ensure_column(engine, table: str, name: str, column_type: str) -> None:
    existing = {c["name"] for c in inspect(engine).get_columns(table)}
    if name not in existing:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}"))


def _ensure_index(engine, statement: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(statement))
    except Exception as exc:
        # Une base existante peut contenir des doublons : on ne casse pas le démarrage.
        logger.warning("Index unique %s non créé : %s", statement, exc)


def _add_missing_columns(engine) -> None:
    """Migration légère idempotente des colonnes et index absents sur les tables existantes.

    ``create_all`` ne met pas à jour le schéma des tables déjà présentes (notamment
    SQLite, qui ne supporte que ``ADD COLUMN``). On rattrape donc ici les colonnes
    manquantes et les index uniques, sans effet si elles sont déjà présentes.
    """
    insp = inspect(engine)
    if not insp.has_table("dongles"):
        return
    _ensure_column(engine, "dongles", "last_error", "VARCHAR")
    _ensure_column(engine, "dongles", "transport", "VARCHAR NOT NULL DEFAULT 'modem'")
    # Nullable, jamais de défaut : une ligne ancienne reste simplement sans serial ADB.
    _ensure_column(engine, "dongles", "identity_adb_serial", "VARCHAR")
    if insp.has_table("proxies"):
        for name, column_type in _PROXY_MIGRATED_COLUMNS:
            _ensure_column(engine, "proxies", name, column_type)
        for statement in _PROXY_MIGRATED_INDEXES:
            _ensure_index(engine, statement)


def init_db(engine):
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


class Database:
    def __init__(self, engine):
        self.engine = engine
        self.session_factory = sessionmaker(bind=engine)

    def __enter__(self):
        self.session = self.session_factory()
        return self.session

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.session.close()