from broxy4g.adapters.modem_manager import ModemInfo
from broxy4g.domain.identity import IdentityMaterial


def _modem(imei="123456789012345", usb_serial=None, iccid=None):
    return ModemInfo(
        path="/org/freedesktop/ModemManager1/Modem/0",
        imei=imei,
        iccid=iccid,
        usb_serial=usb_serial,
        state="disconnected",
        interface_name="wwan0",
    )


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
