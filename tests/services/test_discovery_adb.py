from unittest.mock import AsyncMock, MagicMock

import pytest

from broxy4g.adapters.adb import AdbDevice, FakeAdb
from broxy4g.adapters.modem_manager import FakeModemManager, ModemInfo
from broxy4g.services.discovery import DiscoveryService


def _modem(imei="123456789012345", usb_serial="USB123", iccid="ICCID123"):
    return ModemInfo(
        path="/org/freedesktop/ModemManager1/Modem/0",
        imei=imei,
        iccid=iccid,
        usb_serial=usb_serial,
        state="disconnected",
        interface_name="wwan0",
    )


@pytest.fixture
def fake_modem():
    modem = FakeModemManager()
    modem.add_modem(_modem())
    return modem


@pytest.fixture
def fake_repo():
    repo = AsyncMock()
    repo.get_by_identity_key.return_value = None
    return repo


@pytest.fixture
def fake_adb():
    adb = FakeAdb()
    adb.devices = [AdbDevice(serial="SER123", state="device", model="SM-A217F")]
    return adb


class TestDiscoveryAdb:
    async def test_modems_and_phones_both_listed(self, fake_modem, fake_repo, fake_adb):
        service = DiscoveryService(fake_modem, fake_repo, adb=fake_adb)

        result = await service.list_unenrolled()

        modems = [d for d in result if d.transport == "modem"]
        phones = [d for d in result if d.transport == "adb"]
        assert len(modems) == 1
        assert modems[0].identity_imei == "123456789012345"
        assert len(phones) == 1
        assert phones[0].identity_adb_serial == "SER123"
        assert phones[0].identity_imei is None
        assert phones[0].model == "SM-A217F"
        assert phones[0].suggested_label == "Téléphone SM-A217F"
        assert phones[0].suggested_apn is None
        assert phones[0].interface_name is None

    async def test_unauthorized_and_offline_phones_not_listed(self, fake_modem, fake_repo, fake_adb):
        fake_adb.devices = [
            AdbDevice(serial="SERUNAUTH", state="unauthorized"),
            AdbDevice(serial="SEROFFLINE", state="offline"),
        ]
        service = DiscoveryService(fake_modem, fake_repo, adb=fake_adb)

        result = await service.list_unenrolled()

        assert [d.transport for d in result] == ["modem"]

    async def test_enrolled_phone_serial_filtered(self, fake_modem, fake_repo, fake_adb):
        async def get_by_identity_key(key):
            if key == "SER123":
                return MagicMock()
            return None

        fake_repo.get_by_identity_key.side_effect = get_by_identity_key
        service = DiscoveryService(fake_modem, fake_repo, adb=fake_adb)

        result = await service.list_unenrolled()

        assert all(d.identity_adb_serial != "SER123" for d in result)
        assert [d.transport for d in result] == ["modem"]

    async def test_adb_absent_lists_modems_only(self, fake_modem, fake_repo):
        service = DiscoveryService(fake_modem, fake_repo)

        result = await service.list_unenrolled()

        assert [d.transport for d in result] == ["modem"]

    async def test_list_devices_raising_returns_no_phones(self, fake_modem, fake_repo):
        class DownAdb:
            async def list_devices(self):
                raise RuntimeError("adb server not running")

            async def get_prop(self, serial, prop):
                return None

        service = DiscoveryService(fake_modem, fake_repo, adb=DownAdb())

        result = await service.list_unenrolled()

        assert [d.transport for d in result] == ["modem"]
