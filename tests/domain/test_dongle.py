from datetime import UTC, datetime, timedelta

import pytest

import broxy4g.domain.dongle as dongle_module
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState, InvalidStateTransition
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.transport import Transport


class _AdvancingClock:
    """Deterministic clock: each ``now()`` call advances by one second."""

    def __init__(self) -> None:
        self._current = datetime(2024, 1, 1, tzinfo=UTC)

    def now(self, _tz: object = None) -> datetime:
        self._current += timedelta(seconds=1)
        return self._current


def test_dongle_creation():
    identity = IdentityMaterial(imei="123456789012345")
    dongle = Dongle(identity=identity, label="Test Dongle")
    assert dongle.state == DongleState.DISCOVERED


def test_dongle_default_transport_is_modem():
    identity = IdentityMaterial(imei="123456789012345")
    dongle = Dongle(identity=identity, label="Test Dongle")
    assert dongle.transport is Transport.MODEM


def test_dongle_change_state_valid():
    identity = IdentityMaterial(imei="123456789012345")
    dongle = Dongle(identity=identity, label="Test Dongle")
    dongle.change_state(DongleState.CONFIGURING)
    assert dongle.state == DongleState.CONFIGURING


def test_dongle_change_state_invalid():
    identity = IdentityMaterial(imei="123456789012345")
    dongle = Dongle(identity=identity, label="Test Dongle")
    with pytest.raises(InvalidStateTransition):
        dongle.change_state(DongleState.ONLINE)


def test_dongle_identity_key():
    identity = IdentityMaterial(imei="123456789012345")
    dongle = Dongle(identity=identity, label="Test Dongle")
    assert dongle.identity_key == "123456789012345"


def test_dongle_touch(monkeypatch):
    monkeypatch.setattr(dongle_module, "datetime", _AdvancingClock())
    identity = IdentityMaterial(imei="123456789012345")
    dongle = Dongle(identity=identity, label="Test Dongle")
    old_updated_at = dongle.updated_at
    dongle.touch()
    assert dongle.updated_at > old_updated_at
