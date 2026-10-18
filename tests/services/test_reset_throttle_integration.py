"""Reset avec garde anti-tempête : refus sans toucher le matériel (fake modem)."""

from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.services.reset import ResetService
from broxy4g.services.reset_throttle import DEFAULT_MAX_PER_WINDOW, ResetThrottle


class FakeClock:
    """Horloge factice statique : aucun temps ne s'écoule pendant le test."""

    def __call__(self) -> float:
        return 0.0


async def _enroll_in_state(
    enrollment_service, fake_modem, imei, interface, table_id, fwmark,
    state: DongleState = DongleState.ONLINE,
):
    """Enrôle un dongle dans l'état donné, modem+bearer prêts pour un reset DONE."""
    dongle = (await enrollment_service.enroll(IdentityMaterial(imei=imei), "Test", "test.apn")).dongle
    dongle.state = state
    dongle.interface_name = interface
    dongle.route_table_id = table_id
    dongle.fwmark = fwmark
    await enrollment_service.dongle_repo.save(dongle)
    fake_modem.add_modem(ModemInfo(
        path=interface, imei=imei, iccid=None,
        usb_serial=None, state="connected", interface_name=interface,
    ))
    fake_modem.set_bearer_ip(interface, BearerIpConfig(
        address="10.0.0.1", prefix=30, gateway="10.0.0.2"
    ))
    return dongle


def _make_service(dongle_repo, proxy_repo, connection_service, proxy_service,
                  fake_reset_lock, metrics_snapshot, throttle):
    return ResetService(
        dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock,
        metrics_snapshot=metrics_snapshot,
        throttle=throttle,
        ip_wait_attempts=2,
        ip_wait_delay=0.0,
    )


async def test_second_reset_immediately_is_pending_cooldown_without_hardware_reset(
    enrollment_service, fake_modem, dongle_repo, proxy_repo,
    connection_service, proxy_service, fake_reset_lock, metrics_snapshot, fake_connectivity,
):
    """1er reset exécuté (DONE) ; 2e immédiat : PENDING cooldown, pas de 2e reset modem."""
    fake_connectivity.set_public_ip("10.64.0.1")
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    svc = _make_service(
        dongle_repo, proxy_repo, connection_service, proxy_service,
        fake_reset_lock, metrics_snapshot, ResetThrottle(clock=FakeClock()),
    )

    first = await svc.reset(dongle.id)
    assert first.status == "DONE"

    second = await svc.reset(dongle.id)

    assert second.status == "PENDING"
    assert "cooldown" in (second.message or "")
    assert len(fake_modem.reset_calls) == 1  # aucun 2e reset matériel


async def test_reset_beyond_window_cap_is_error_without_hardware_reset(
    enrollment_service, fake_modem, dongle_repo, proxy_repo,
    connection_service, proxy_service, fake_reset_lock, metrics_snapshot, fake_connectivity,
):
    """Plafond de fenêtre atteint : ERROR « refusé », le modem n'est jamais touché."""
    fake_connectivity.set_public_ip("10.64.0.1")
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    throttle = ResetThrottle(clock=FakeClock())
    for _ in range(DEFAULT_MAX_PER_WINDOW):  # pré-enregistre le plafond de la fenêtre
        throttle.record(str(dongle.id))

    svc = _make_service(
        dongle_repo, proxy_repo, connection_service, proxy_service,
        fake_reset_lock, metrics_snapshot, throttle,
    )
    result = await svc.reset(dongle.id)

    assert result.status == "ERROR"
    assert "refusé" in (result.message or "")
    assert fake_modem.reset_calls == []
