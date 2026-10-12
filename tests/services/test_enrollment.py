from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from passlib.hash import md5_crypt

from broxy4g.adapters.modem_manager import FakeModemManager, ModemInfo
from broxy4g.domain.apn_resolution import ApnSuggestion
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import Proxy, ProxySource
from broxy4g.repositories.settings_repository import AUTO_CREATE_PROXY_KEY
from broxy4g.services.enrollment import EnrollmentService
from broxy4g.services.proxy_provisioning import ProvisionedProxy


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
    dongle = (await enrollment_service.enroll(identity, "Test Dongle", "test.apn")).dongle
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
    dongle = (await enrollment_service.enroll(identity, "Test", None)).dongle
    await enrollment_service.deactivate(dongle.id)
    result = await enrollment_service.get_by_id(dongle.id)
    assert result is None


@pytest.mark.asyncio
async def test_deactivate_removes_proxy(enrollment_service, proxy_service):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", None)).dongle
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
    dongle = (await enrollment_service.enroll(identity, "Old", None)).dongle
    updated = await enrollment_service.update(dongle.id, "New", "new.apn", None)
    assert updated.label == "New"
    assert updated.apn == "new.apn"


@pytest.mark.asyncio
async def test_enroll_resolves_apn_when_omitted(dongle_repo, connection_service, proxy_service):
    service = EnrollmentService(
        dongle_repo, connection_service, proxy_service,
        modem=_modem(), apn_source=FakeApnSource("free"),
    )
    dongle = (await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", None)).dongle
    assert dongle.apn == "free"


@pytest.mark.asyncio
async def test_enroll_treats_empty_apn_as_omitted(dongle_repo, connection_service, proxy_service):
    service = EnrollmentService(
        dongle_repo, connection_service, proxy_service,
        modem=_modem(), apn_source=FakeApnSource("free"),
    )
    dongle = (await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", "")).dongle
    assert dongle.apn == "free"


@pytest.mark.asyncio
async def test_enroll_keeps_user_apn(dongle_repo, connection_service, proxy_service):
    service = EnrollmentService(
        dongle_repo, connection_service, proxy_service,
        modem=_modem(), apn_source=FakeApnSource("free"),
    )
    dongle = (await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", "custom.apn")).dongle
    assert dongle.apn == "custom.apn"


@pytest.mark.asyncio
async def test_enroll_without_apn_source_leaves_apn_none(dongle_repo, connection_service, proxy_service):
    service = EnrollmentService(dongle_repo, connection_service, proxy_service, modem=_modem())
    dongle = (await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", None)).dongle
    assert dongle.apn is None


@pytest.mark.asyncio
async def test_enroll_apn_none_when_modem_identity_unknown(dongle_repo, connection_service, proxy_service):
    service = EnrollmentService(
        dongle_repo, connection_service, proxy_service,
        modem=_modem(imei="999999999999999"), apn_source=FakeApnSource("free"),
    )
    dongle = (await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", None)).dongle
    assert dongle.apn is None


@pytest.mark.asyncio
async def test_enroll_returns_credentials_when_new_proxy_created(enrollment_service, proxy_service):
    result = await enrollment_service.enroll(IdentityMaterial(imei="123456789012345"), "Test", None)
    creds = result.proxy_credentials
    assert creds is not None
    assert creds.username
    assert creds.password
    proxy = await proxy_service.get_by_dongle_id(result.dongle.id)
    assert proxy is not None
    assert proxy.auth_user == creds.username
    assert md5_crypt.verify(creds.password, proxy.password_hash)


@pytest.mark.asyncio
async def test_enroll_credentials_none_when_auto_create_disabled(enrollment_service, settings_repo):
    await settings_repo.set_bool(AUTO_CREATE_PROXY_KEY, False)
    result = await enrollment_service.enroll(IdentityMaterial(imei="123456789012345"), "Test", None)
    assert result.dongle.state == DongleState.CONFIGURING
    assert result.proxy_credentials is None


@pytest.mark.asyncio
async def test_enroll_credentials_none_when_proxy_pre_exists(dongle_repo, connection_service, settings_repo):
    """Chemin idempotent : un proxy déjà existant ne régénère pas d'identifiants."""
    existing = Proxy(
        dongle_id=None,
        container_name="broxy4g-proxy-x",
        listen_port=31201,
        source_type=ProxySource.DONGLE,
        source_ref="123456789012345",
    )
    proxy_stub = AsyncMock()
    proxy_stub.create.return_value = ProvisionedProxy(proxy=existing)
    service = EnrollmentService(dongle_repo, connection_service, proxy_stub, settings_repo=settings_repo)

    result = await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", None)

    assert result.dongle.state == DongleState.CONFIGURING
    assert result.proxy_credentials is None


@pytest.mark.asyncio
async def test_enroll_succeeds_when_proxy_creation_fails(dongle_repo, connection_service, settings_repo):
    """Un échec de provisioning ne casse pas l'enrôlement : credentials None."""
    proxy_stub = AsyncMock()
    proxy_stub.create.side_effect = RuntimeError("docker indisponible")
    service = EnrollmentService(dongle_repo, connection_service, proxy_stub, settings_repo=settings_repo)

    result = await service.enroll(IdentityMaterial(imei="123456789012345"), "Test", None)

    assert result.dongle.state == DongleState.CONFIGURING
    assert result.proxy_credentials is None
