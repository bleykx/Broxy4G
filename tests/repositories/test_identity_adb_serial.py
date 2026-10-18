"""Persistance du serial ADB : le matériel ADB-only doit survivre à l'aller-retour dépôt.

Un téléphone (ex. SM-A217F sans root) ne fournit ni IMEI ni ICCID : son identité
est uniquement ``adb_serial``. Si la colonne ``identity_adb_serial`` n'est pas
persistée, la lecture renvoie une identité vide et ``primary_key`` lève
``ValueError`` — ce qui cassait la reconciliation et le reset après reboot.
"""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.transport import Transport
from broxy4g.repositories import DongleRepository
from broxy4g.repositories.database import init_db

ADB_SERIAL = "R58N729DZMV"


@pytest.mark.asyncio
async def test_adb_only_dongle_round_trip(dongle_repo):
    """ADB-only : adb_serial ET identity_key intacts après save → get_by_id."""
    dongle = Dongle(
        identity=IdentityMaterial(adb_serial=ADB_SERIAL),
        label="téléphone",
        transport=Transport.ADB,
    )
    saved = await dongle_repo.save(dongle)

    retrieved = await dongle_repo.get_by_id(saved.id)
    assert retrieved is not None
    assert retrieved.identity.adb_serial == ADB_SERIAL
    assert retrieved.identity_key == ADB_SERIAL
    assert retrieved.identity.primary_key == ADB_SERIAL  # ne lève plus de ValueError


@pytest.mark.asyncio
async def test_adb_only_second_save_updates_in_place(dongle_repo):
    """Un deuxième save du même dongle ADB-only met à jour sans dupliquer."""
    first = await dongle_repo.save(
        Dongle(identity=IdentityMaterial(adb_serial=ADB_SERIAL), label="téléphone",
               transport=Transport.ADB)
    )
    second = await dongle_repo.save(
        Dongle(identity=IdentityMaterial(adb_serial=ADB_SERIAL), label="téléphone 2",
               transport=Transport.ADB)
    )

    assert second.id == first.id
    all_dongles = await dongle_repo.list_all()
    assert len(all_dongles) == 1
    assert all_dongles[0].label == "téléphone 2"


@pytest.mark.asyncio
async def test_usb_serial_and_adb_serial_both_restored(dongle_repo):
    """usb_serial + adb_serial : les deux reviennent de la base."""
    await dongle_repo.save(
        Dongle(
            identity=IdentityMaterial(usb_serial="ABC123", adb_serial=ADB_SERIAL),
            label="téléphone",
            transport=Transport.ADB,
        )
    )

    retrieved = await dongle_repo.get_by_identity_key("ABC123")
    assert retrieved is not None
    assert retrieved.identity.usb_serial == "ABC123"
    assert retrieved.identity.adb_serial == ADB_SERIAL
    assert retrieved.identity_key == "ABC123"  # priorité usb_serial > adb_serial


@pytest.mark.asyncio
async def test_legacy_row_without_adb_serial_column_still_reads():
    """Base ancienne sans la colonne identity_adb_serial : après migration, la
    ligne se lit avec adb_serial None, sans exception."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE dongles (
                    id VARCHAR PRIMARY KEY,
                    identity_imei VARCHAR, identity_usb_serial VARCHAR,
                    identity_iccid VARCHAR,
                    identity_key VARCHAR UNIQUE,
                    label VARCHAR, state VARCHAR,
                    apn VARCHAR, apn_auth VARCHAR,
                    route_table_id INTEGER, fwmark INTEGER,
                    interface_name VARCHAR, proxy_id VARCHAR, last_error VARCHAR,
                    created_at DATETIME, updated_at DATETIME
                )
                """
            )
        )
    init_db(engine)  # la migration idempotente ajoute identity_adb_serial (NULL)
    dongle_id = uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dongles (id, identity_key, label, state) "
                "VALUES (:id, '123', 'Ancien dongle', 'ONLINE')"
            ),
            {"id": str(dongle_id)},
        )

    repo = DongleRepository(sessionmaker(bind=engine))
    retrieved = await repo.get_by_id(dongle_id)
    assert retrieved is not None
    assert retrieved.identity.adb_serial is None


@pytest.mark.asyncio
async def test_init_db_exposes_identity_adb_serial_column():
    """Le schéma in-memory créé par init_db expose la nouvelle colonne."""
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("dongles")}
    assert "identity_adb_serial" in columns
