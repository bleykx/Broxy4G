import logging

from broxy4g.adapters.adb import AdbDevice, FakeAdb
from broxy4g.workers.discovery import DiscoveryWorker

LOGGER = "broxy4g.workers.discovery"


def _phone_announcements(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("Téléphone ADB détecté")]


async def test_phone_announced_once_not_reannounced(fake_modem, dongle_repo, caplog):
    adb = FakeAdb()
    adb.devices = [AdbDevice(serial="SER123", state="device", model="SM-A217F")]
    worker = DiscoveryWorker(fake_modem, dongle_repo, adb=adb)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await worker.discover()
        await worker.discover()

    announcements = _phone_announcements(caplog)
    assert len(announcements) == 1
    assert "SER123" in announcements[0]
    assert "Téléphone SM-A217F" in announcements[0]


async def test_absent_adb_logs_nothing_about_phones(fake_modem, dongle_repo, caplog):
    worker = DiscoveryWorker(fake_modem, dongle_repo)  # adb=None

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await worker.discover()

    assert _phone_announcements(caplog) == []
