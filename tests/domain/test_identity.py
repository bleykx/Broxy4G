import pytest

from broxy4g.domain.identity import IdentityMaterial


def test_primary_key_returns_imei():
    identity = IdentityMaterial(imei="123456789012345", usb_serial="ABC123", iccid="9876543210987654321")
    assert identity.primary_key == "123456789012345"


def test_primary_key_returns_usb_serial():
    identity = IdentityMaterial(usb_serial="ABC123", iccid="9876543210987654321")
    assert identity.primary_key == "ABC123"


def test_primary_key_returns_iccid():
    identity = IdentityMaterial(iccid="9876543210987654321")
    assert identity.primary_key == "9876543210987654321"


def test_primary_key_returns_adb_serial():
    identity = IdentityMaterial(adb_serial="R58N12AB")
    assert identity.primary_key == "R58N12AB"


def test_primary_key_precedence_imei_over_adb_serial():
    identity = IdentityMaterial(imei="123456789012345", adb_serial="R58N12AB")
    assert identity.primary_key == "123456789012345"


def test_primary_key_precedence_usb_serial_over_adb_serial():
    identity = IdentityMaterial(usb_serial="ABC123", adb_serial="R58N12AB")
    assert identity.primary_key == "ABC123"


def test_primary_key_precedence_adb_serial_over_iccid():
    identity = IdentityMaterial(iccid="9876543210987654321", adb_serial="R58N12AB")
    assert identity.primary_key == "R58N12AB"


def test_primary_key_raises_if_empty():
    identity = IdentityMaterial()
    with pytest.raises(ValueError):
        _ = identity.primary_key


def test_is_complete():
    assert IdentityMaterial(imei="123456789012345").is_complete
    assert IdentityMaterial(adb_serial="R58N12AB").is_complete
    assert not IdentityMaterial().is_complete


def test_matches():
    identity1 = IdentityMaterial(imei="123456789012345")
    identity2 = IdentityMaterial(imei="123456789012345")
    assert identity1.matches(identity2)

    identity3 = IdentityMaterial(imei="123456789012346")
    assert not identity1.matches(identity3)


def test_matches_with_adb_serial():
    identity1 = IdentityMaterial(adb_serial="R58N12AB")
    identity2 = IdentityMaterial(adb_serial="R58N12AB")
    assert identity1.matches(identity2)

    identity3 = IdentityMaterial(adb_serial="R58N99ZZ")
    assert not identity1.matches(identity3)
