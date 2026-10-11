from uuid import uuid4

import pytest

from broxy4g.adapters.modem_manager import FakeModemManager, ModemInfo
from broxy4g.domain.apn_resolution import ApnSuggestion
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.services.enrollment import EnrollmentService


class FakeApnSource:
    def __init__(self, apn: str | None):
        self._apn = apn

    def resolve(self, imsi, operator_id, operator_name) -> ApnSuggestion | None:
        if self._apn is None:
            return None
        return ApnSuggestion(apn=self._apn, operator="Test")


def _modem(imei: str = "123456789012345") -> FakeModemManager:
    modem = FakeModemManager()
    modem.add_modem(ModemInfo(
        path="/modem/0",
        imei=imei,
        iccid=None,
        usb_serial=None,
        state="registered",
        interface_name="wwan0",
        imsi="208150043462938",
        operator_id="20815",
        operator_name="Free",
    ))
    return modem


@pytest.mark.asyncio
async def test_enroll_creates_dongle_in_configuring_state(enrollment_service):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = await enrollment_service.enroll(identity, "Test Dongle", "test.apn")
    assert dongle.state == DongleState.CONFIGURING
    assert dongle.label == "Test Dongle"
    assert dongle.apn == "test.apn"


@pytest.mark.asyncio
async def test_enroll_raises_if_identity_incomplete(enrollment_service):
    identity = IdentityMaterial()
    with pytest.raises(ValueError, match="incomplete"):
        await enrollment_service.enroll(identity, "Test", None)


@pytest.mark.asyncio
async def test_enroll_raises_if_already_enrolled(enrollment_service):
    identity = IdentityMaterial(imei="123456789012345")
    await enrollment_service.enroll(identity, "Test", None)
    with pytest.raises(ValueError, match="already enrolled"):
        await enrollment_service.enroll(identity, "Test2", None)


@pytest.mark.asyncio
async def test_deactivate_deletes_dongle(enrollment_service):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = await enrollment_service.enroll(identity, "Test", None)
    await enrollment_service.deactivate(dongle.id)
    result = await enrollment_service.get_by_id(dongle.id)
    assert result is None


@pytest.mark.asyncio
async def test_deactivate_removes_proxy(enrollment_service, proxy_service):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = await enrollment_service.enroll(identity, "Test", None)
    assert await proxy_service.get_by_dongle_id(dongle.id) is not None

    await enrollment_service.deactivate(dongle.id)

    result = await enrollment_service.get_by_id(dongle.id)
    assert result is None
    proxy = await proxy_service.get_by_dongle_id(dongle.id)
    assert proxy is None


@pytest.mark.asyncio
async def test_deactivate_raises_when_not_found(enrollment_service):
    with pytest.raises(ValueError, match="Dongle not found"):
        await enrollment_service.deactivate(uuid4())


@pytest.mark.asyncio
async def test_update_modifies_label_and_apn(enrollment_service):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = await enrollment_service.enroll(identity, "Old", None)
    updated = await enrollment_service.update(dongle.id, "New", "new.apn", None)
    assert updated.label == "New"
    assert updated.apn == "new.apn"


@pytest.mark.asyncio
async def test_enroll_resolves_apn_when_omitted(dongle_repo, connection_service, proxy_service):
    service = EnrollmentService(
        dongle_repo, connection_service, proxy_service,
        modem=_modem(), apn_source=FakeApnSource("free"),
    )
    dongle = await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", None)
    assert dongle.apn == "free"


@pytest.mark.asyncio
async def test_enroll_treats_empty_apn_as_omitted(dongle_repo, connection_service, proxy_service):
    service = EnrollmentService(
        dongle_repo, connection_service, proxy_service,
        modem=_modem(), apn_source=FakeApnSource("free"),
    )
    dongle = await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", "")
    assert dongle.apn == "free"


@pytest.mark.asyncio
async def test_enroll_keeps_user_apn(dongle_repo, connection_service, proxy_service):
    service = EnrollmentService(
        dongle_repo, connection_service, proxy_service,
        modem=_modem(), apn_source=FakeApnSource("free"),
    )
    dongle = await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", "custom.apn")
    assert dongle.apn == "custom.apn"


@pytest.mark.asyncio
async def test_enroll_without_apn_source_leaves_apn_none(dongle_repo, connection_service, proxy_service):
    service = EnrollmentService(dongle_repo, connection_service, proxy_service, modem=_modem())
    dongle = await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", None)
    assert dongle.apn is None


@pytest.mark.asyncio
async def test_enroll_apn_none_when_modem_identity_unknown(dongle_repo, connection_service, proxy_service):
    service = EnrollmentService(
        dongle_repo, connection_service, proxy_service,
        modem=_modem(imei="999999999999999"), apn_source=FakeApnSource("free"),
    )
    dongle = await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", None)
    assert dongle.apn is None
