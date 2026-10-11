from unittest.mock import AsyncMock, MagicMock

import pytest

from broxy4g.domain.apn_resolution import ApnSuggestion
from broxy4g.services.discovery import DiscoveryService


class FakeApnSource:
    def __init__(self, apn: str | None):
        self._apn = apn

    def resolve(self, imsi, operator_id, operator_name) -> ApnSuggestion | None:
        if self._apn is None:
            return None
        return ApnSuggestion(apn=self._apn, operator="Test")


@pytest.fixture
def mock_modem():
    return AsyncMock()


@pytest.fixture
def mock_dongle_repo():
    return AsyncMock()


@pytest.fixture
def discovery_service(mock_modem, mock_dongle_repo):
    return DiscoveryService(mock_modem, mock_dongle_repo)


class TestDiscoveryService:
    async def test_list_unenrolled_returns_unenrolled_modems_with_correct_fields(self, discovery_service, mock_modem, mock_dongle_repo):
        # Given
        modem1 = MagicMock(imei="123456789012345", usb_serial="USB123", iccid="ICCID123", interface_name="wwan0")
        modem2 = MagicMock(imei="987654321098765", usb_serial="USB456", iccid="ICCID456", interface_name="wwan1")
        mock_modem.list_modems.return_value = [modem1, modem2]
        
        # Mock repository to return None for first identity (not enrolled) and existing dongle for second (enrolled)
        mock_dongle_repo.get_by_identity_key.side_effect = [None, MagicMock()]
        
        # When
        result = await discovery_service.list_unenrolled()
        
        # Then
        assert len(result) == 1
        assert result[0].identity_imei == "123456789012345"
        assert result[0].identity_usb_serial == "USB123"
        assert result[0].identity_iccid == "ICCID123"
        assert result[0].interface_name == "wwan0"
        assert result[0].suggested_label == "Dongle 2345"

    async def test_list_unenrolled_excludes_modems_already_in_db(self, discovery_service, mock_modem, mock_dongle_repo):
        # Given
        modem = MagicMock(imei="123456789012345", usb_serial="USB123", iccid="ICCID123", interface_name="wwan0")
        mock_modem.list_modems.return_value = [modem]
        
        # Mock repository to return existing dongle (already enrolled)
        mock_dongle_repo.get_by_identity_key.return_value = MagicMock()
        
        # When
        result = await discovery_service.list_unenrolled()
        
        # Then
        assert len(result) == 0

    async def test_list_unenrolled_skips_modems_with_incomplete_identity(self, discovery_service, mock_modem, mock_dongle_repo):
        # Given
        # Test with a modem with no identity material at all
        modem1 = MagicMock(imei=None, usb_serial=None, iccid=None, interface_name="wwan0")
        # Test with a modem with complete identity (has IMEI)
        modem2 = MagicMock(imei="123456789012345", usb_serial=None, iccid=None, interface_name="wwan1")
        mock_modem.list_modems.return_value = [modem1, modem2]
        
        # Mock repository to return None for both (not enrolled)
        mock_dongle_repo.get_by_identity_key.return_value = None
        
        # When
        result = await discovery_service.list_unenrolled()
        
        # Then
        # Only modem2 should be included (has IMEI), modem1 should be skipped due to incomplete identity
        assert len(result) == 1
        assert result[0].identity_imei == "123456789012345"

    async def test_suggested_apn_is_none_without_source(self, discovery_service, mock_modem, mock_dongle_repo):
        modem = MagicMock(
            imei="123456789012345", usb_serial=None, iccid=None,
            interface_name="wwan0", imsi="208150043462938", operator_id="20815", operator_name="Free",
        )
        mock_modem.list_modems.return_value = [modem]
        mock_dongle_repo.get_by_identity_key.return_value = None

        result = await discovery_service.list_unenrolled()

        assert result[0].suggested_apn is None

    async def test_suggested_apn_from_source(self, mock_modem, mock_dongle_repo):
        service = DiscoveryService(mock_modem, mock_dongle_repo, apn_source=FakeApnSource("free"))
        modem = MagicMock(
            imei="123456789012345", usb_serial=None, iccid=None,
            interface_name="wwan0", imsi="208150043462938", operator_id="20815", operator_name="Free",
        )
        mock_modem.list_modems.return_value = [modem]
        mock_dongle_repo.get_by_identity_key.return_value = None

        result = await service.list_unenrolled()

        assert result[0].suggested_apn == "free"