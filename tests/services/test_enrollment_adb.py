from unittest.mock import AsyncMock, MagicMock

import pytest

from broxy4g.adapters.modem_manager import FakeModemManager, ModemInfo
from broxy4g.domain.apn_resolution import ApnSuggestion
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.transport import Transport
from broxy4g.services.enrollment import EnrollmentService


def _modem(imei="123456789012345", usb_serial="USB123", iccid="ICCID123"):
    return ModemInfo(
        path="/org/freedesktop/ModemManager1/Modem/0",
        imei=imei,
        iccid=iccid,
        usb_serial=usb_serial,
        state="disconnected",
        interface_name="wwan0",
    )


def _service(dongle_repo, connection_service, modem, apn_source):
    # proxy_service=None : aucune création automatique de proxy dans ces tests.
    return EnrollmentService(
        dongle_repo,
        connection_service,
        None,
        modem=modem,
        apn_source=apn_source,
    )


@pytest.fixture
def dongle_repo():
    return AsyncMock()


@pytest.fixture
def connection_service():
    return AsyncMock()


@pytest.fixture
def apn_source():
    return MagicMock()


class TestEnrollmentAdb:
    async def test_adb_enrollment(
        self, dongle_repo, connection_service, apn_source,
    ):
        fake_modem = AsyncMock()
        dongle_repo.get_by_identity_key.return_value = None
        reloaded = MagicMock(name="reloaded")
        dongle_repo.get_by_id.return_value = reloaded
        service = _service(dongle_repo, connection_service, fake_modem, apn_source)

        result = await service.enroll(
            IdentityMaterial(adb_serial="SER123"), "Téléphone", None, transport=Transport.ADB
        )

        # Transport ADB persisté sur le dongle construit.
        saved = dongle_repo.save.await_args.args[0]
        assert saved.transport is Transport.ADB
        assert saved.apn is None
        # Aucune résolution d'APN ModemManager pour un téléphone.
        apn_source.resolve.assert_not_called()
        fake_modem.list_modems.assert_not_called()
        # Activation déléguée une seule fois au service de connexion.
        connection_service.connect.assert_awaited_once_with(saved.id)
        # Le résultat porte le dongle rechargé (état final réel).
        assert result.dongle is reloaded
        dongle_repo.get_by_id.assert_awaited_once_with(saved.id)

    async def test_adb_enrollment_survives_connect_failure(
        self, dongle_repo, connection_service, apn_source,
    ):
        connection_service.connect.side_effect = RuntimeError("tethering impossible")
        dongle_repo.get_by_identity_key.return_value = None
        reloaded = MagicMock(name="reloaded")
        dongle_repo.get_by_id.return_value = reloaded
        service = _service(dongle_repo, connection_service, AsyncMock(), apn_source)

        result = await service.enroll(
            IdentityMaterial(adb_serial="SER123"), "Téléphone", None, transport=Transport.ADB
        )

        assert result.dongle is reloaded

    async def test_adb_enrollment_without_connection_service(
        self, dongle_repo, apn_source,
    ):
        service = EnrollmentService(dongle_repo, None, None)
        dongle_repo.get_by_identity_key.return_value = None

        result = await service.enroll(
            IdentityMaterial(adb_serial="SER123"), "Téléphone", None, transport=Transport.ADB
        )

        # L'activation est ignorée mais l'enrôlement aboutit.
        saved = dongle_repo.save.await_args.args[0]
        assert result.dongle is saved

    async def test_modem_enrollment_unchanged(
        self, dongle_repo, connection_service, apn_source,
    ):
        fake_modem = FakeModemManager()
        fake_modem.add_modem(_modem())
        apn_source.resolve.return_value = ApnSuggestion(apn="free", operator="Free")
        dongle_repo.get_by_identity_key.return_value = None
        service = _service(dongle_repo, connection_service, fake_modem, apn_source)

        result = await service.enroll(
            IdentityMaterial(imei="123456789012345"), "Dongle", None
        )

        # Transport par défaut MODEM et résolution d'APN inchangée.
        assert result.dongle.transport is Transport.MODEM
        assert result.dongle.apn == "free"
        apn_source.resolve.assert_called_once()
        connection_service.connect.assert_not_awaited()
        dongle_repo.get_by_id.assert_not_awaited()
