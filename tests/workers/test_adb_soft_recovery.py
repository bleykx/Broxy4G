"""Récupération douce ADB : attente bornée puis relance bornée du serveur adb.

Prouve le correctif du défaut « serveur adb stale après reboot » (priorité n°2)
sur un adaptateur simulé (aucun appel système) :
- device présent        → aucune relance ;
- device absent         → exactement une relance, succès après relance ;
- relance en échec      → WARNING d'échec, aucune exception propagée ;
- deux cycles rapprochés → une seule relance (cooldown par serial).
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from broxy4g.adapters.adb import AdbDevice, FakeAdb
from broxy4g.domain.transport import Transport
from broxy4g.workers.adb_soft_recovery import AdbSoftRecovery

SERIAL = "R58N729DZMV"


class ControllableClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_dongle(serial: str = SERIAL):
    return SimpleNamespace(
        id="dongle-1",
        transport=Transport.ADB,
        identity=SimpleNamespace(adb_serial=serial, primary_key=serial),
    )


def make_recovery(adb: FakeAdb, clock=None, restart_cooldown: float = 60.0) -> AdbSoftRecovery:
    return AdbSoftRecovery(
        adb=adb, smartphone=None, dongle_repo=None, usb_recovery=None,
        restart_cooldown=restart_cooldown, clock=clock or ControllableClock(),
    )


@pytest.mark.asyncio
async def test_device_present_no_restart():
    """Device déjà visible : l'attente passe, le serveur adb n'est JAMAIS relancé."""
    fake = FakeAdb()
    fake.devices = [AdbDevice(serial=SERIAL, state="device")]
    recovery = make_recovery(fake)

    await recovery.attempt_soft_recovery(make_dongle())

    assert fake.restart_count == 0


@pytest.mark.asyncio
async def test_device_absent_single_restart_then_success():
    """Device absent : exactement UNE relance, puis l'attente passe après relance."""
    fake = FakeAdb()  # aucun device visible
    recovery = make_recovery(fake)

    with patch.object(
        fake, "wait_for_device", new=AsyncMock(side_effect=[False, True])
    ) as mock_wait:
        await recovery.attempt_soft_recovery(make_dongle())  # ne doit pas lever

    assert fake.restart_count == 1
    assert mock_wait.await_count == 2  # attente initiale + attente après relance


@pytest.mark.asyncio
async def test_restart_failure_logs_warning_without_exception(caplog):
    """Relance en échec : WARNING d'échec logué, aucune exception, cycle intact."""
    fake = FakeAdb()
    fake.fail_restart_times = 1  # restart_server() retourne False
    recovery = make_recovery(fake)

    with caplog.at_level(logging.INFO):
        await recovery.attempt_soft_recovery(make_dongle())  # ne doit pas lever

    assert fake.restart_count == 1
    failures = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "échouée" in r.getMessage()
    ]
    assert failures, "le WARNING d'échec de relance doit être logué"


@pytest.mark.asyncio
async def test_two_close_cycles_single_restart_cooldown(caplog):
    """Deux cycles rapprochés : la relance n'est faite qu'une fois (cooldown par serial)."""
    fake = FakeAdb()  # device jamais revenu, relance « réussie » mais device absent
    clock = ControllableClock()
    recovery = make_recovery(fake, clock=clock)

    with caplog.at_level(logging.INFO):
        await recovery.attempt_soft_recovery(make_dongle())  # cycle 1 : relance
        clock.t += 1.0  # cycle 2 : 1 s plus tard, toujours dans le cooldown de 60 s
        await recovery.attempt_soft_recovery(make_dongle())

    assert fake.restart_count == 1  # une seule relance malgré deux échecs d'attente
    cooldown = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "cooldown" in r.getMessage()
    ]
    assert cooldown, "le second cycle doit loguer le refus de relance (cooldown)"
