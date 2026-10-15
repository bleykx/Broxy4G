import logging
import time

import pytest

from broxy4g.adapters.modem_manager import ModemInfo
from broxy4g.adapters.usb_device import USB_DEVICES_ROOT, FakeUsbDevice
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.services.usb_recovery import UsbRecoveryService

IMEI = "123456789012345"
MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
DEVICE_PATH = "/sys/devices/pci0000:00/0000:00:14.0/usb1/1-1"

_STATE_PATHS = {
    DongleState.DISCOVERED: [],
    DongleState.UNENROLLED: [DongleState.UNENROLLED],
    DongleState.CONFIGURING: [DongleState.CONFIGURING],
    DongleState.CONNECTING: [DongleState.CONFIGURING, DongleState.CONNECTING],
    DongleState.ONLINE: [
        DongleState.CONFIGURING, DongleState.CONNECTING, DongleState.ONLINE,
    ],
    DongleState.DEGRADED: [
        DongleState.CONFIGURING, DongleState.CONNECTING,
        DongleState.ONLINE, DongleState.DEGRADED,
    ],
    DongleState.RESETTING: [
        DongleState.CONFIGURING, DongleState.ERROR, DongleState.RESETTING,
    ],
    DongleState.ERROR: [DongleState.CONFIGURING, DongleState.ERROR],
    DongleState.OFFLINE: [DongleState.CONFIGURING, DongleState.OFFLINE],
}


async def _save_dongle(dongle_repo, state: DongleState) -> Dongle:
    dongle = Dongle(identity=IdentityMaterial(imei=IMEI), label="Test")
    for next_state in _STATE_PATHS[state]:
        dongle.change_state(next_state)
    await dongle_repo.save(dongle)
    return dongle


def _add_modem(fake_modem, device: str | None = DEVICE_PATH):
    fake_modem.add_modem(ModemInfo(
        path=MODEM_PATH, imei=IMEI, iccid=None, usb_serial=None,
        state="disconnected", interface_name="wwan0", device_path=device,
    ))


def _wire_reevaluation(fake_usb_device, fake_modem):
    """La ré-autorisation simule la ré-énumération noyau (vanish → retour)."""
    authorized_file = fake_usb_device.register(DEVICE_PATH)
    fake_usb_device.on_reenumeration = (
        lambda: fake_modem.simulate_usb_reevaluation(MODEM_PATH)
    )
    return authorized_file


@pytest.mark.parametrize(
    "state",
    [
        DongleState.RESETTING,
        DongleState.CONFIGURING,
        DongleState.DISCOVERED,
        DongleState.UNENROLLED,
        DongleState.CONNECTING,
        DongleState.ONLINE,
        DongleState.DEGRADED,
    ],
)
async def test_rejected_from_each_forbidden_state(
    dongle_repo, fake_modem, fake_usb_device, usb_recovery, state
):
    """La récupération USB est refusée depuis tout état non éligible."""
    await _save_dongle(dongle_repo, state)
    _add_modem(fake_modem)
    _wire_reevaluation(fake_usb_device, fake_modem)

    dongle = (await dongle_repo.list_all())[0]
    result = await usb_recovery.attempt(dongle.id)

    assert result.attempted is False
    assert result.reason == f"état {state.value} inéligible"
    assert fake_usb_device.writes == []


async def test_not_attempted_when_reset_lock_held(
    dongle_repo, fake_modem, fake_usb_device, usb_recovery, fake_reset_lock
):
    """Jamais de tentative si le verrou de reset est tenu pour ce dongle."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    authorized_file = _wire_reevaluation(fake_usb_device, fake_modem)
    fake_reset_lock.acquire(str(dongle.id))

    result = await usb_recovery.attempt(dongle.id)

    assert result.attempted is False
    assert result.reason == "reset en cours"
    assert fake_usb_device.writes == []
    assert await fake_usb_device.read_authorized(authorized_file) is True


async def test_success_when_modem_comes_back(
    dongle_repo, fake_modem, fake_usb_device, usb_recovery
):
    """Nominal : toggle 0→1, le modem réapparaît, résultat recovered."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    authorized_file = _wire_reevaluation(fake_usb_device, fake_modem)

    result = await usb_recovery.attempt(dongle.id)

    assert result.attempted is True
    assert result.reappeared is True
    assert fake_usb_device.writes == [(authorized_file, False), (authorized_file, True)]
    # Idempotence d'état final : authorized finit à 1.
    assert await fake_usb_device.read_authorized(authorized_file) is True


async def test_offline_also_eligible(
    dongle_repo, fake_modem, fake_usb_device, usb_recovery
):
    """OFFLINE (dongle enrôlé) est aussi éligible."""
    dongle = await _save_dongle(dongle_repo, DongleState.OFFLINE)
    _add_modem(fake_modem)
    _wire_reevaluation(fake_usb_device, fake_modem)

    result = await usb_recovery.attempt(dongle.id)

    assert result.attempted is True
    assert result.reappeared is True


async def test_one_attempt_per_cycle_then_retry_next_cycle(
    dongle_repo, fake_modem, fake_usb_device, usb_recovery
):
    """Au plus une tentative par dongle et par cycle ; au cycle suivant, une nouvelle."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    authorized_file = _wire_reevaluation(fake_usb_device, fake_modem)

    first = await usb_recovery.attempt(dongle.id)
    second = await usb_recovery.attempt(dongle.id)
    usb_recovery.begin_cycle()
    third = await usb_recovery.attempt(dongle.id)

    assert first.attempted is True
    assert second.attempted is False
    assert second.reason == "déjà tenté ce cycle"
    assert third.attempted is True
    assert len(fake_usb_device.writes) == 4  # deux toggles 0→1, pas trois
    assert fake_usb_device.writes[2] == (authorized_file, False)


async def test_timeout_when_modem_does_not_come_back(
    dongle_repo, fake_modem, fake_usb_device, usb_recovery, caplog
):
    """Timeout borné : WARNING explicite, pas de boucle, authorized finit à 1."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    authorized_file = fake_usb_device.register(DEVICE_PATH)
    fake_usb_device.on_reenumeration = (
        lambda: fake_modem.forget(MODEM_PATH)  # le modem ne revient jamais
    )

    with caplog.at_level(logging.WARNING, logger="broxy4g.services.usb_recovery"):
        result = await usb_recovery.attempt(dongle.id)

    assert result.attempted is True
    assert result.reappeared is False
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("n'est pas réapparu" in m for m in warnings)
    assert fake_usb_device.writes == [(authorized_file, False), (authorized_file, True)]
    assert await fake_usb_device.read_authorized(authorized_file) is True


class _AlwaysAuthorizedUsb(FakeUsbDevice):
    """Le device est réautorisé en permanence : chaque lecture renvoie 1."""

    async def read_authorized(self, authorized_file: str) -> bool:
        return True


async def test_no_redundant_write_when_already_authorized(
    dongle_repo, fake_modem, usb_recovery
):
    """Idempotence : si ``authorized`` vaut déjà ``1``, on ne le réécrit pas.

    Le toggle reste bien tenté (l'écriture ``0`` est utile) mais l'écriture
    ``1`` sur un fichier déjà à ``1`` est sautée : aucune réécriture inutile.
    """
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    always_on = _AlwaysAuthorizedUsb()
    always_on.register(DEVICE_PATH, authorized=True)
    always_on.on_reenumeration = (
        lambda: fake_modem.simulate_usb_reevaluation(MODEM_PATH)
    )
    usb_recovery.usb = always_on  # remplacer l'adaptateur du service

    result = await usb_recovery.attempt(dongle.id)

    assert result.attempted is True
    assert always_on.writes == [
        (f"{USB_DEVICES_ROOT}/1-1/authorized", False)
    ]  # le seul 0 ; le 1 déjà présent n'a pas été réécrit


async def test_toggle_from_authorized_one_still_reenumerates(
    dongle_repo, fake_modem, fake_usb_device, usb_recovery
):
    """Device branché (authorized=1) : le toggle 0→1 est l'action utile."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    authorized_file = fake_usb_device.register(DEVICE_PATH, authorized=True)
    fake_usb_device.on_reenumeration = (
        lambda: fake_modem.simulate_usb_reevaluation(MODEM_PATH)
    )

    result = await usb_recovery.attempt(dongle.id)

    assert result.attempted is True
    assert result.reappeared is True
    assert fake_usb_device.writes == [(authorized_file, False), (authorized_file, True)]


async def test_unresolvable_device_path_is_safe_noop(
    dongle_repo, fake_modem, fake_usb_device, usb_recovery, caplog
):
    """Chemin USB introuvable : no-op journalisé, aucun effet de bord, pas de crash."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem, device=None)  # ModemManager sans chemin device

    with caplog.at_level(logging.WARNING, logger="broxy4g.services.usb_recovery"):
        result = await usb_recovery.attempt(dongle.id)

    assert result.attempted is False
    assert result.reason == "périphérique USB introuvable"
    assert fake_usb_device.writes == []
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("introuvable" in m for m in warnings)


async def test_no_modem_in_mmcli_is_safe_noop(
    dongle_repo, fake_usb_device, usb_recovery
):
    """Modem absent de ModemManager : no-op, aucun effet de bord."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)

    result = await usb_recovery.attempt(dongle.id)

    assert result.attempted is False
    assert result.reason == "périphérique USB introuvable"
    assert fake_usb_device.writes == []


async def test_authorized_ends_at_one_even_if_reauth_fails(
    dongle_repo, fake_modem, fake_usb_device, usb_recovery, monkeypatch
):
    """Si la ré-autorisation échoue, une nouvelle tentative laisse authorized à 1."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    authorized_file = fake_usb_device.register(DEVICE_PATH)
    fake_usb_device.on_reenumeration = lambda: fake_modem.forget(MODEM_PATH)

    calls = {"n": 0}

    async def flaky_write(authorized_file_, value):
        if value and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("sysfs busy")
        # Méthode de classe directe : l'attribut d'instance shadowed
        # recréerait une récursion.
        await FakeUsbDevice.write_authorized(fake_usb_device, authorized_file_, value)

    monkeypatch.setattr(usb_recovery.usb, "write_authorized", flaky_write)

    result = await usb_recovery.attempt(dongle.id)

    assert result.attempted is True
    assert await fake_usb_device.read_authorized(authorized_file) is True
    assert (authorized_file, True) in fake_usb_device.writes  # la 2e tentative a écrit


def _service_with_cooldown(dongle_repo, uplink_routes, usb, reset_lock, cooldown_seconds=300.0):
    """Un service de récupération avec un vrai backoff inter-cycles.

    Le faux partagé (``usb_recovery``) désactive le cooldown (0 s) pour isoler
    la dédup par cycle ; ici on le (ré)active pour tester le bornage temporel.
    """
    return UsbRecoveryService(
        dongle_repo,
        uplink_routes,
        usb,
        reset_lock,
        reappearance_timeout=0.2,
        poll_delay=0.02,
        cooldown_seconds=cooldown_seconds,
    )


async def test_failed_attempt_sets_cross_cycle_cooldown(
    dongle_repo, uplink_routes, fake_modem, fake_usb_device, fake_reset_lock
):
    """Une tentative échouée horodate le cooldown : l'essai suivant est reporté
    sans re-énumérer (aucun nouvel appel à l'adaptateur USB)."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    authorized_file = fake_usb_device.register(DEVICE_PATH)
    fake_usb_device.on_reenumeration = lambda: fake_modem.forget(MODEM_PATH)
    service = _service_with_cooldown(dongle_repo, uplink_routes, fake_usb_device, fake_reset_lock)

    first = await service.attempt(dongle.id)
    assert first.attempted is True
    assert first.reappeared is False  # le modem ne réapparaît jamais
    assert fake_usb_device.writes == [(authorized_file, False), (authorized_file, True)]

    service.begin_cycle()  # nouveau cycle : la dédup par cycle est remise à zéro
    second = await service.attempt(dongle.id)
    assert second.attempted is False
    assert "cooldown" in second.reason  # c'est le backoff inter-cycles qui bloque
    assert len(fake_usb_device.writes) == 2  # l'adaptateur n'a pas été re-énuméré


async def test_cooldown_elapse_allows_retry(
    dongle_repo, uplink_routes, fake_modem, fake_usb_device, fake_reset_lock
):
    """Après expiration du cooldown, la ré-énumération est de nouveau tentée."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    _wire_reevaluation(fake_usb_device, fake_modem)
    service = _service_with_cooldown(
        dongle_repo, uplink_routes, fake_usb_device, fake_reset_lock, cooldown_seconds=300.0
    )

    first = await service.attempt(dongle.id)
    assert first.attempted is True

    service.begin_cycle()  # réinitialise la dédup par cycle, pas le cooldown
    blocked = await service.attempt(dongle.id)
    assert blocked.attempted is False
    assert "cooldown" in blocked.reason

    # Fait avancer l'horloge au-delà du cooldown sans attendre réellement.
    service._last_attempt_at[dongle.id] = time.monotonic() - 301
    retry = await service.attempt(dongle.id)
    assert retry.attempted is True


async def test_begin_cycle_does_not_clear_cooldown(
    dongle_repo, uplink_routes, fake_modem, fake_usb_device, fake_reset_lock
):
    """``begin_cycle()`` ne remet pas à zéro le cooldown inter-cycles."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    _wire_reevaluation(fake_usb_device, fake_modem)
    service = _service_with_cooldown(
        dongle_repo, uplink_routes, fake_usb_device, fake_reset_lock, cooldown_seconds=300.0
    )

    await service.attempt(dongle.id)
    service.begin_cycle()

    assert dongle.id in service._last_attempt_at  # l'horodatage est conservé
    second = await service.attempt(dongle.id)
    assert second.attempted is False
    assert "cooldown" in second.reason


async def test_cooldowns_are_per_dongle_independent(
    dongle_repo, uplink_routes, fake_modem, fake_usb_device, fake_reset_lock
):
    """Le cooldown d'un dongle n'en bloque pas un autre (indépendance)."""
    async def save(imei: str) -> Dongle:
        d = Dongle(identity=IdentityMaterial(imei=imei), label="Test")
        d.change_state(DongleState.CONFIGURING)
        d.change_state(DongleState.ERROR)
        await dongle_repo.save(d)
        return d

    d1 = await save("111111111111111")
    d2 = await save("222222222222222")
    fake_modem.add_modem(ModemInfo(
        path="/org/freedesktop/ModemManager1/Modem/1", imei="111111111111111",
        iccid=None, usb_serial=None, state="disconnected", interface_name="wwan0",
        device_path="/sys/devices/usb1/1-1",
    ))
    fake_modem.add_modem(ModemInfo(
        path="/org/freedesktop/ModemManager1/Modem/2", imei="222222222222222",
        iccid=None, usb_serial=None, state="disconnected", interface_name="wwan0",
        device_path="/sys/devices/usb1/1-2",
    ))
    fake_usb_device.register("/sys/devices/usb1/1-1")
    fake_usb_device.register("/sys/devices/usb1/1-2")
    service = _service_with_cooldown(dongle_repo, uplink_routes, fake_usb_device, fake_reset_lock)

    first1 = await service.attempt(d1.id)  # horodate le cooldown de d1
    first2 = await service.attempt(d2.id)  # NON bloqué par le cooldown de d1
    assert first1.attempted is True
    assert first2.attempted is True

    service.begin_cycle()
    assert "cooldown" in (await service.attempt(d1.id)).reason  # d1 bloqué
    assert "cooldown" in (await service.attempt(d2.id)).reason  # d2 bloqué (sien)


async def test_successful_first_attempt_still_works_with_cooldown(
    dongle_repo, uplink_routes, fake_modem, fake_usb_device, fake_reset_lock
):
    """Happy path : une première tentative réussie pose le cooldown sans l'altérer."""
    dongle = await _save_dongle(dongle_repo, DongleState.ERROR)
    _add_modem(fake_modem)
    authorized_file = _wire_reevaluation(fake_usb_device, fake_modem)
    service = _service_with_cooldown(dongle_repo, uplink_routes, fake_usb_device, fake_reset_lock)

    result = await service.attempt(dongle.id)

    assert result.attempted is True
    assert result.reappeared is True
    assert dongle.id in service._last_attempt_at  # cooldown posé
    assert fake_usb_device.writes == [(authorized_file, False), (authorized_file, True)]
