import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from broxy4g.adapters.connectivity import FakeConnectivity
from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.post_done_window import ResetPostDoneWindow
from broxy4g.domain.proxy import ProxyState
from broxy4g.services.reset import ResetService


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


@pytest.mark.asyncio
async def test_reset_returns_done_on_success(
    reset_service, enrollment_service, connection_service, fake_modem, fake_iface
):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle

    # Set dongle to ONLINE so reset can transition to RESETTING
    dongle.state = DongleState.ONLINE
    dongle.interface_name = "wwan0"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    await enrollment_service.dongle_repo.save(dongle)

    fake_modem.add_modem(ModemInfo(
        path="wwan0", imei="123456789012345", iccid=None,
        usb_serial=None, state="connected", interface_name="wwan0",
    ))
    # Bearer IPv4 configure la reconnexion : connect l'assigne après purge de l'interface.
    fake_modem.set_bearer_ip("wwan0", BearerIpConfig(
        address="10.0.0.1", prefix=30, gateway="10.0.0.2"
    ))

    result = await reset_service.reset(dongle.id)
    assert result.status == "DONE"


@pytest.mark.asyncio
async def test_reset_success_recycles_enabled_proxy_container(
    reset_service, enrollment_service, proxy_repo, fake_modem, fake_docker_compose
):
    """Proxy activé avant le reset : le reset met le conteneur en pause puis le
    recrée (down puis up) ; l'état désiré ``enabled`` est conservé."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    assert proxy is not None and proxy.enabled is True

    calls_before = len(fake_docker_compose.calls)
    result = await reset_service.reset(dongle.id)

    assert result.status == "DONE"
    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored.enabled is True
    assert stored.state == ProxyState.RUNNING
    actions = [
        action for (name, action) in fake_docker_compose.calls[calls_before:]
        if name == proxy.container_name
    ]
    # pause (down) puis recréation du conteneur (up)
    assert actions == ["down", "up"]


@pytest.mark.asyncio
async def test_reset_failure_keeps_enabled_proxy_stopped(
    reset_service, enrollment_service, connection_service, proxy_repo,
    fake_modem, fake_docker_compose,
):
    """Échec de reconnexion : l'état désiré du proxy reste ``enabled = True``,
    mais le reset ne relance PAS son conteneur (la réconciliation s'en chargera
    dès que le dongle sera de nouveau utilisable)."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    assert proxy is not None and proxy.enabled is True

    async def fake_connect(dongle_id):
        d = await enrollment_service.dongle_repo.get_by_id(dongle_id)
        d.state = DongleState.ONLINE  # garantit que la transition vers ERROR est autorisée
        d.fail("connect KO")
        await enrollment_service.dongle_repo.save(d)
        return d

    calls_before = len(fake_docker_compose.calls)
    with patch.object(connection_service, "connect", new=AsyncMock(side_effect=fake_connect)):
        result = await reset_service.reset(dongle.id)

    assert result.status == "ERROR"
    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored.enabled is True
    assert stored.state == ProxyState.STOPPED
    actions = [
        action for (name, action) in fake_docker_compose.calls[calls_before:]
        if name == proxy.container_name
    ]
    # seul le down de la pause : aucun up après l'échec
    assert actions == ["down"]


@pytest.mark.asyncio
async def test_reset_timeout_keeps_proxy_enabled(
    enrollment_service, connection_service, dongle_repo, proxy_repo, proxy_service,
    fake_modem, fake_reset_lock,
):
    """Timeout du reset : ERROR « reset expiré », mais l'état désiré du proxy
    reste ``enabled = True`` (la réconciliation le restaurera)."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    assert proxy is not None and proxy.enabled is True

    async def blocking_connect(dongle_id):
        await asyncio.sleep(3600)  # ne rend jamais la main

    svc = ResetService(
        dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock,
        reset_timeout=0.01,
    )
    with patch.object(connection_service, "connect", new=AsyncMock(side_effect=blocking_connect)):
        result = await svc.reset(dongle.id)

    assert result.status == "ERROR"
    assert "reset expiré" in (result.message or "")
    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored.enabled is True


@pytest.mark.asyncio
async def test_reset_never_starts_proxy_disabled_before_reset(
    reset_service, enrollment_service, proxy_service, proxy_repo,
    fake_modem, fake_docker_compose,
):
    """Proxy désactivé par l'opérateur avant le reset : il reste
    ``enabled = False`` après le reset et son conteneur n'est jamais lancé."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    proxy = await proxy_service.get_by_dongle_id(dongle.id)
    assert proxy is not None
    await proxy_service.stop(proxy.id)  # état désiré : désactivé
    assert (await proxy_repo.get_by_id(proxy.id)).enabled is False

    calls_before = len(fake_docker_compose.calls)
    result = await reset_service.reset(dongle.id)

    assert result.status == "DONE"
    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored.enabled is False
    assert stored.state == ProxyState.STOPPED
    actions = [
        action for (name, action) in fake_docker_compose.calls[calls_before:]
        if name == proxy.container_name
    ]
    # jamais de up : le conteneur n'est pas (re)lancé
    assert "up" not in actions


@pytest.mark.asyncio
async def test_reset_returns_pending_if_already_in_progress(reset_service, enrollment_service, fake_reset_lock):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", None)).dongle
    fake_reset_lock.acquire(str(dongle.id))
    result = await reset_service.reset(dongle.id)
    assert result.status == "PENDING"


@pytest.mark.asyncio
async def test_reset_releases_lock_even_on_error(reset_service, enrollment_service, fake_reset_lock):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", None)).dongle
    await reset_service.reset(dongle.id)
    assert not fake_reset_lock.is_locked(str(dongle.id))


@pytest.mark.asyncio
async def test_reset_returns_error_with_message_on_connect_failure(
    reset_service, enrollment_service, connection_service, fake_modem
):
    """Si connect lève après le reset modem, reset retourne ERROR avec le détail."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )

    with patch.object(connection_service, "connect", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await reset_service.reset(dongle.id)

    assert result.status == "ERROR"
    assert "RuntimeError: boom" in (result.message or "")


@pytest.mark.asyncio
async def test_reset_reports_error_when_connect_leaves_dongle_in_error(
    reset_service, enrollment_service, connection_service, fake_modem
):
    """Si connect ne lève pas mais laisse le dongle en ERROR, reset retourne ERROR + détail."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )

    async def fake_connect(dongle_id):
        d = await enrollment_service.dongle_repo.get_by_id(dongle_id)
        d.state = DongleState.ONLINE  # garantit que la transition vers ERROR est autorisée
        d.fail("connect KO")
        await enrollment_service.dongle_repo.save(d)
        return d

    with patch.object(connection_service, "connect", new=AsyncMock(side_effect=fake_connect)):
        result = await reset_service.reset(dongle.id)

    assert result.status == "ERROR"
    assert "connect KO" in (result.message or "")


@pytest.mark.asyncio
async def test_reset_many_two_dongles_both_done(reset_service, enrollment_service, fake_modem):
    d1 = await _enroll_in_state(enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1)
    d2 = await _enroll_in_state(enrollment_service, fake_modem, "987654321098765", "wwan1", 10002, 2)

    results = await reset_service.reset_many([d1.id, d2.id])

    by_id = dict(results)
    assert set(by_id) == {d1.id, d2.id}
    assert by_id[d1.id].status == "DONE"
    assert by_id[d2.id].status == "DONE"


@pytest.mark.asyncio
async def test_reset_many_pending_when_already_locked(
    reset_service, enrollment_service, fake_modem, fake_reset_lock
):
    locked = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    other = await _enroll_in_state(
        enrollment_service, fake_modem, "987654321098765", "wwan1", 10002, 2
    )
    fake_reset_lock.acquire(str(locked.id))  # un reset tourne déjà sur `locked`

    results = await reset_service.reset_many([locked.id, other.id])

    by_id = dict(results)
    assert by_id[locked.id].status == "PENDING"
    assert by_id[other.id].status == "DONE"


@pytest.mark.asyncio
async def test_reset_many_unknown_id_errors_without_affecting_others(
    reset_service, enrollment_service, fake_modem
):
    d = await _enroll_in_state(enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1)
    unknown = uuid4()

    results = await reset_service.reset_many([unknown, d.id])

    by_id = dict(results)
    assert by_id[unknown].status == "ERROR"
    assert "dongle introuvable" in (by_id[unknown].message or "")
    assert by_id[d.id].status == "DONE"


@pytest.mark.asyncio
async def test_reset_many_deduplicates_ids(reset_service, enrollment_service, fake_modem):
    d = await _enroll_in_state(enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1)

    results = await reset_service.reset_many([d.id, d.id])

    assert len(results) == 1
    assert results[0][0] == d.id
    assert results[0][1].status == "DONE"


@pytest.mark.asyncio
@pytest.mark.parametrize("start_state", [DongleState.OFFLINE, DongleState.ERROR, DongleState.DEGRADED])
async def test_reset_recovery_from_offline_error_degraded(
    reset_service, enrollment_service, fake_modem, start_state
):
    """M3 : le reset est une action de récupération depuis OFFLINE / ERROR / DEGRADED."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1, state=start_state
    )

    result = await reset_service.reset(dongle.id)

    assert result.status == "DONE"


@pytest.mark.asyncio
async def test_reset_forbidden_state_returns_clean_error(reset_service, enrollment_service, fake_modem):
    """M3 : un état non récupérable (DISCOVERED) renvoie un message propre, pas le jargon technique."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1,
        state=DongleState.DISCOVERED,
    )

    result = await reset_service.reset(dongle.id)

    assert result.status == "ERROR"
    assert result.message == "reset impossible dans l'état DISCOVERED"
    assert "InvalidStateTransition" not in (result.message or "")


@pytest.mark.asyncio
async def test_reset_many_one_error_one_done(reset_service, enrollment_service, fake_modem):
    """Un dongle dont connect échoue réellement (modem introuvable) → ERROR, l'autre → DONE."""
    d_good = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    # Aucun modem pour ce dongle : connect laisse le dongle en ERROR.
    d_bad = (await enrollment_service.enroll(IdentityMaterial(imei="987654321098765"), "Test", "test.apn")).dongle
    d_bad.state = DongleState.ONLINE
    d_bad.interface_name = "wwan2"
    d_bad.route_table_id = 10002
    d_bad.fwmark = 2
    await enrollment_service.dongle_repo.save(d_bad)

    results = await reset_service.reset_many([d_good.id, d_bad.id])

    by_id = dict(results)
    assert by_id[d_good.id].status == "DONE"
    assert by_id[d_bad.id].status == "ERROR"
    assert by_id[d_bad.id].message  # détail présent


@pytest.mark.asyncio
async def test_reset_timeout_errors_and_releases_lock(
    enrollment_service, connection_service, dongle_repo, proxy_repo, proxy_service,
    fake_modem, fake_reset_lock,
):
    """N2 : un reset coincé expire (ERROR « reset expiré ») et le verrou est libéré."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )

    async def blocking_connect(dongle_id):
        await asyncio.sleep(3600)  # ne rend jamais la main

    svc = ResetService(
        dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock,
        reset_timeout=0.05,
    )
    with patch.object(connection_service, "connect", new=AsyncMock(side_effect=blocking_connect)):
        result = await svc.reset(dongle.id)

    assert result.status == "ERROR"
    assert "reset expiré" in (result.message or "")
    # Le verrou est libéré : re-acquire possible.
    token = fake_reset_lock.acquire(str(dongle.id))
    fake_reset_lock.release(str(dongle.id), token)


@pytest.mark.asyncio
async def test_reset_refreshes_metrics_snapshot(
    reset_service, enrollment_service, fake_modem, fake_connectivity, metrics_snapshot
):
    """Le reset rafraîchit le cache de métriques lu par la liste des dongles."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    fake_connectivity.set_public_ip("2.3.4.5")

    result = await reset_service.reset(dongle.id)

    assert result.status == "DONE"
    assert metrics_snapshot.get(dongle.id).public_ip == "2.3.4.5"


@pytest.mark.asyncio
async def test_reset_errors_when_modem_does_not_reappear(
    reset_service, enrollment_service, connection_service, fake_modem
):
    """Si le modem ne réapparaît pas après la ré-énumération USB, le reset est en erreur."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    fake_modem.hide_on_reset("wwan0")

    result = await reset_service.reset(dongle.id)

    assert result.status == "ERROR"
    assert "réapparu" in (result.message or "")


@pytest.mark.asyncio
async def test_reset_calls_modem_reset(reset_service, enrollment_service, fake_modem):
    """Le reset passe par un vrai reset modem USB (adaptateur reset_modem)."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )

    result = await reset_service.reset(dongle.id)

    assert result.status == "DONE"
    assert fake_modem.reset_calls == ["wwan0"]


@pytest.mark.asyncio
async def test_reset_success_never_passes_through_offline(
    reset_service, enrollment_service, fake_modem, dongle_repo
):
    """Le reset réussi reste RESETTING (teardown sans OFFLINE) puis finit ONLINE.

    Passer par OFFLINE briserait l'interlock de réconciliation pendant la
    fenêtre modem-absent : un connect tenterait et échouerait.
    """
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )

    visited: list[DongleState] = []
    original_change_state = Dongle.change_state

    def spy_change_state(self, new_state):
        visited.append(new_state)
        return original_change_state(self, new_state)

    with patch.object(Dongle, "change_state", spy_change_state):
        result = await reset_service.reset(dongle.id)

    assert result.status == "DONE"
    assert fake_modem.reset_calls == ["wwan0"]
    assert DongleState.OFFLINE not in visited
    final = await dongle_repo.get_by_id(dongle.id)
    assert final.state == DongleState.ONLINE


@pytest.mark.asyncio
async def test_reset_done_on_cgnat_same_public_ip(
    reset_service, enrollment_service, fake_modem, fake_connectivity
):
    """CGNAT : la même IP publique avant/après n'est pas une erreur (session toujours renouvelée)."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    fake_connectivity.set_public_ip("9.9.9.9")

    result = await reset_service.reset(dongle.id)

    assert result.status == "DONE"


class _ControllableClock:
    """Horloge contrôlable pour la fenêtre post-DONE."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _LatePublicIpConnectivity(FakeConnectivity):
    """Sonde d'IP publique qui échoue ``misses`` fois avant de retourner l'IP."""

    def __init__(self, misses: int, ip: str):
        super().__init__()
        self._misses = misses
        self._ip = ip
        self.probe_calls = 0

    async def get_public_ip(self, interface=None):
        self.interface_calls.append(interface)
        self.probe_calls += 1
        if self.probe_calls <= self._misses:
            return None
        return self._ip


@pytest.mark.asyncio
async def test_reset_errors_when_public_ip_not_propagated(
    enrollment_service, connection_service, dongle_repo, proxy_repo, proxy_service,
    fake_modem, fake_reset_lock,
):
    """Gate IP : sans IP publique propagée, le reset est ERROR et le dongle reste ONLINE.

    Marquer ERROR déclencherait une ré-énumération USB destructive (usb_recovery)
    sur un lien qui fonctionne : l'état du dongle ne doit pas bouger.
    """
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    # Pas de ``set_public_ip`` : la sonde échoue à chaque essai.
    svc = ResetService(
        dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock,
        ip_wait_attempts=2, ip_wait_delay=0.0,
    )

    result = await svc.reset(dongle.id)

    assert result.status == "ERROR"
    assert "IP publique" in (result.message or "")
    stored = await dongle_repo.get_by_id(dongle.id)
    assert stored.state == DongleState.ONLINE


@pytest.mark.asyncio
async def test_reset_done_after_public_ip_appears_late(
    enrollment_service, connection_service, dongle_repo, proxy_repo, proxy_service,
    fake_modem, fake_reset_lock, fake_connectivity,
):
    """IP publique qui propage en retard : la boucle bornée re-sonde jusqu'à OK → DONE."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    # Les 2 premières sondes (baseline + métriques) retournent None, puis l'IP.
    double = _LatePublicIpConnectivity(misses=2, ip="198.51.100.7")
    connection_service.diagnostics.probe.connectivity = double

    svc = ResetService(
        dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock,
        ip_wait_attempts=2, ip_wait_delay=0.0,
    )

    result = await svc.reset(dongle.id)

    assert result.status == "DONE"
    assert double.probe_calls >= 3


@pytest.mark.asyncio
async def test_reset_post_done_window_returns_pending_for_new_reset(
    enrollment_service, connection_service, dongle_repo, proxy_repo, proxy_service,
    fake_modem, fake_reset_lock, fake_connectivity,
):
    """Après un DONE, une nouvelle demande dans la fenêtre post-DONE renvoie PENDING
    sans re-resetter le modem ; après expiration, le reset aboutit de nouveau."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    fake_connectivity.set_public_ip("10.64.0.1")
    clock = _ControllableClock()
    svc = ResetService(
        dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock,
        ip_wait_attempts=1, ip_wait_delay=0.0,
        done_window=ResetPostDoneWindow(seconds=15.0, clock=clock),
    )

    first = await svc.reset(dongle.id)
    assert first.status == "DONE"

    second = await svc.reset(dongle.id)
    assert second.status == "PENDING"
    assert "fenêtre de répit" in (second.message or "")
    assert len(fake_modem.reset_calls) == 1  # la fenêtre ne re-reset pas le matériel

    clock.t = 16.0
    third = await svc.reset(dongle.id)
    assert third.status == "DONE"
    assert len(fake_modem.reset_calls) == 2


@pytest.mark.asyncio
async def test_post_done_window_refusals_never_remark(
    enrollment_service, connection_service, dongle_repo, proxy_repo, proxy_service,
    fake_modem, fake_reset_lock, fake_connectivity,
):
    """Les refus par la fenêtre ne ré-horodent pas : la fenêtre expire au même
    instant (t + 15 s depuis le DONE), quel que soit le nombre de refus."""
    dongle = await _enroll_in_state(
        enrollment_service, fake_modem, "123456789012345", "wwan0", 10001, 1
    )
    fake_connectivity.set_public_ip("10.64.0.1")
    clock = _ControllableClock()
    svc = ResetService(
        dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock,
        ip_wait_attempts=1, ip_wait_delay=0.0,
        done_window=ResetPostDoneWindow(seconds=15.0, clock=clock),
    )

    assert (await svc.reset(dongle.id)).status == "DONE"
    assert (await svc.reset(dongle.id)).status == "PENDING"  # refus à t=0
    clock.t = 7.0
    assert (await svc.reset(dongle.id)).status == "PENDING"  # refus consécutif à t=7
    clock.t = 16.0
    # Si un refus ré-horodait, la fenêtre expirerait à 7 + 15 = 22, pas 15.
    assert (await svc.reset(dongle.id)).status == "DONE"
