import logging

from broxy4g.adapters.modem_manager import ModemInfo
from broxy4g.domain.identity import IdentityMaterial

LOGGER = "broxy4g.workers.discovery"


def _modem(imei="123456789012345", usb_serial=None, iccid=None,
           path="/org/freedesktop/ModemManager1/Modem/0"):
    return ModemInfo(
        path=path,
        imei=imei,
        iccid=iccid,
        usb_serial=usb_serial,
        state="disconnected",
        interface_name="wwan0",
    )


def _announcements(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("Nouveau dongle détecté")]


async def test_discover_finds_new_dongle(discovery_worker, fake_modem, dongle_repo):
    fake_modem.add_modem(_modem())
    result = await discovery_worker.discover()
    assert len(result) == 1
    assert result[0].imei == "123456789012345"


async def test_discover_skips_enrolled_dongle(discovery_worker, fake_modem, dongle_repo, enrollment_service):
    identity = IdentityMaterial(imei="123456789012345")
    await enrollment_service.enroll(identity, "Test", None)
    fake_modem.add_modem(_modem())
    result = await discovery_worker.discover()
    assert len(result) == 0


async def test_discover_skips_no_identity(discovery_worker, fake_modem):
    fake_modem.add_modem(_modem(imei=None, usb_serial=None, iccid=None))
    result = await discovery_worker.discover()
    assert len(result) == 0


async def test_discover_multiple_modems(discovery_worker, fake_modem):
    fake_modem.add_modem(ModemInfo(
        path="/org/freedesktop/ModemManager1/Modem/0",
        imei="111", iccid=None, usb_serial=None, state="disconnected", interface_name="wwan0",
    ))
    fake_modem.add_modem(ModemInfo(
        path="/org/freedesktop/ModemManager1/Modem/1",
        imei="222", iccid=None, usb_serial=None, state="disconnected", interface_name="wwan1",
    ))
    result = await discovery_worker.discover()
    assert len(result) == 2


async def test_discover_logs_same_dongle_once_across_polls(discovery_worker, fake_modem, caplog):
    fake_modem.add_modem(_modem())
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await discovery_worker.discover()
        await discovery_worker.discover()
    announcements = _announcements(caplog)
    assert len(announcements) == 1
    assert "123456789012345" in announcements[0]


async def test_discover_announces_late_dongle_once(discovery_worker, fake_modem, caplog):
    fake_modem.add_modem(_modem(imei="111"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await discovery_worker.discover()
        first = _announcements(caplog)
        assert len(first) == 1
        assert "111" in first[0]
        fake_modem.add_modem(_modem(imei="222",
                                    path="/org/freedesktop/ModemManager1/Modem/1"))
        await discovery_worker.discover()
        await discovery_worker.discover()
    announcements = _announcements(caplog)
    assert len(announcements) == 2
    assert sum("222" in msg for msg in announcements) == 1


async def test_discover_reannounces_reappearing_dongle_once(discovery_worker, fake_modem, caplog):
    fake_modem.add_modem(_modem())
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await discovery_worker.discover()
        fake_modem.forget("/org/freedesktop/ModemManager1/Modem/0")
        await discovery_worker.discover()  # dongle disparu : aucune nouvelle annonce
        assert len(_announcements(caplog)) == 1
        fake_modem.add_modem(_modem())
        await discovery_worker.discover()  # réapparition : annoncée une fois
        await discovery_worker.discover()  # mais pas deux fois
    announcements = _announcements(caplog)
    assert len(announcements) == 2
