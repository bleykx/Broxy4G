"""ResetService réel sur un dongle téléphone (transport ADB).

Le contrat métier (DONE/PENDING/ERROR, verrou, fenêtre post-DONE, états
interdits) est prouvé en faisant réellement transiter le reset par la pile
ADB : ``FakeAdb``, ``FakeNetworkManager``, ``FakeRouting``, ``FakeInterface``
et un arbre sysfs factice dans ``tmp_path``.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from broxy4g.adapters.adb import AdbDevice, FakeAdb
from broxy4g.adapters.connectivity import FakeConnectivity
from broxy4g.adapters.modem_manager import FakeModemManager
from broxy4g.adapters.net_interface import FakeInterface
from broxy4g.adapters.network_manager import FakeNetworkManager, NmIpv4
from broxy4g.adapters.routing import FakeRouting
from broxy4g.domain.degraded_arbiter import ProbeVerdict
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.post_done_window import ResetPostDoneWindow
from broxy4g.domain.transport import Transport
from broxy4g.services.connection import ConnectionService
from broxy4g.services.reset import ResetService
from broxy4g.services.smartphone_connection import SmartphoneConnectionService
from broxy4g.services.smartphone_uplink import SmartphoneUplinkService
from broxy4g.services.uplink_routes import UplinkRouteService

SERIAL = "ABC123"
IFACE = "enx02360c7c147a"
PROFILE = f"broxy4g-phone-{SERIAL}"
BEARER_IP = "192.168.172.78"
GATEWAY = "192.168.172.245"


def make_sysfs(tmp_path, serial=SERIAL, iface=IFACE) -> str:
    """Arbre sysfs minimal : device USB 1-2 → 1-2:1.0/net/<iface>."""
    root = tmp_path / "sysfs"
    device = root / "1-2"
    net = device / "1-2:1.0" / "net" / iface
    net.mkdir(parents=True)
    (device / "serial").write_text(serial + "\n")
    (net / "address").write_text("02:36:0c:7c:14:7a\n")
    (net / "operstate").write_text("up\n")
    return str(root)


class OrderingAdb(FakeAdb):
    """Journalise le serial de chaque cycle radio (les appels bruts ne le portent pas)."""

    def __init__(self, events):
        super().__init__()
        self.events = events

    async def airplane_cycle(self, serial, delay=5.0):
        self.events.append(("airplane", serial))
        return await super().airplane_cycle(serial, delay)


class OrderingNm(FakeNetworkManager):
    """Journalise les arrêts de profil : le bail doit être libéré avant la coupure radio."""

    def __init__(self, events):
        super().__init__()
        self.events = events

    async def down(self, name):
        self.events.append(("nm_down", name))
        return await super().down(name)


class LatePublicIpConnectivity(FakeConnectivity):
    """Sonde d'IP publique qui échoue ``misses`` fois avant de retourner l'IP."""

    def __init__(self, misses, ip):
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


class ControllableClock:
    """Horloge contrôlable pour la fenêtre post-DONE."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


async def make_phone(dongle_repo, state=DongleState.ONLINE, serial=SERIAL, adb_only=False):
    """Téléphone persisté avec slot de routage.

    Par défaut, identité ADB + numéro de série USB à la même valeur : c'est le
    cas réel d'un téléphone branché en USB (``adb devices`` renvoie le serial
    USB). Le numéro de série USB survit à l'aller-retour dépôt ; le service ADB
    résout le serial via ``adb_serial or primary_key`` (fallback documenté).
    Avec ``adb_only=True``, seul le serial ADB est porté : le cas matériel où le
    téléphone ne fournit ni IMEI ni ICCID (ex. SM-A217F sans root).
    """
    identity = (
        IdentityMaterial(adb_serial=serial)
        if adb_only
        else IdentityMaterial(usb_serial=serial, adb_serial=serial)
    )
    dongle = Dongle(
        identity=identity,
        label="téléphone",
        state=state,
        transport=Transport.ADB,
        route_table_id=10001,
        fwmark=7,
        interface_name=IFACE,
    )
    return await dongle_repo.save(dongle)


def make_connection(tmp_path, dongle_repo, routing_allocator, isolation,
                    fake_nft, connectivity, events, serial=SERIAL):
    """ConnectionService réelle branchée sur la pile téléphone (fakes ADB/NM/iface/routing)."""
    profile = f"broxy4g-phone-{serial}"
    adb = OrderingAdb(events)
    adb.devices = [AdbDevice(serial=serial, state="device")]
    nm = OrderingNm(events)
    nm.profile_ips[profile] = NmIpv4(BEARER_IP, 24)
    nm.gateways[profile] = GATEWAY
    iface = FakeInterface()
    iface.set_address(IFACE, BEARER_IP)
    routing = FakeRouting()
    uplink = SmartphoneUplinkService(
        adb, nm, iface, routing,
        sysfs_root=make_sysfs(tmp_path, serial=serial),
        iface_wait_attempts=2, iface_wait_delay=0.0,
        dhcp_wait_attempts=2, dhcp_wait_delay=0.0,
    )
    smartphone = SmartphoneConnectionService(
        dongle_repo, uplink, iface, routing, routing_allocator,
        UplinkRouteService(FakeModemManager(), routing),
        isolation=isolation, ip_wait_attempts=3, ip_wait_delay=0.0,
    )
    return ConnectionService(
        dongle_repo, FakeModemManager(), iface, routing, fake_nft, connectivity,
        routing_allocator, UplinkRouteService(FakeModemManager(), routing),
        isolation=isolation,
        modem_wait_attempts=3, modem_wait_delay=0.0,
        smartphone=smartphone,
    ), adb, nm


def make_reset(dongle_repo, proxy_repo, connection, proxy_service, lock,
               snapshot=None, **kwargs):
    # ``ip_wait_delay=0.0`` : la boucle d'attente d'IP publique ne brûle pas de temps réel.
    return ResetService(
        dongle_repo, proxy_repo, connection, proxy_service, lock,
        metrics_snapshot=snapshot, ip_wait_attempts=2, ip_wait_delay=0.0, **kwargs,
    )


@pytest.mark.asyncio
async def test_reset_adb_nominal_done_probe_late_then_ok(
    tmp_path, dongle_repo, proxy_repo, proxy_service, fake_reset_lock,
    metrics_snapshot, isolation, routing_allocator, fake_nft,
):
    """(a) Téléphone ONLINE, sonde non-OK puis OK : DONE ; cycle radio au serial du
    téléphone, et le bail DHCP libéré AVANT la coupure radio (nouveau bail frais)."""
    events: list[tuple[str, str]] = []
    connectivity = LatePublicIpConnectivity(misses=2, ip="198.51.100.7")
    connection, adb, nm = make_connection(
        tmp_path, dongle_repo, routing_allocator, isolation, fake_nft, connectivity, events
    )
    await nm.ensure_profile(IFACE, PROFILE)  # profil existant : le reset peut libérer le bail
    dongle = await make_phone(dongle_repo)

    # (g) session data inconnue pour un téléphone, avant le reset, sans exception.
    assert await connection.get_session_id(dongle.id) is None

    svc = make_reset(dongle_repo, proxy_repo, connection, proxy_service, fake_reset_lock, metrics_snapshot)
    result = await svc.reset(dongle.id)

    assert result.status == "DONE"
    # Le cycle radio (mode avion) a été invoqué avec le serial ADB du téléphone.
    assert ("airplane", SERIAL) in events
    assert ["cmd", "connectivity", "airplane-mode", "enable"] in adb.calls
    assert ["cmd", "connectivity", "airplane-mode", "disable"] in adb.calls
    # Le bail a été libéré AVANT le cycle radio.
    assert events.index(("nm_down", PROFILE)) < events.index(("airplane", SERIAL))
    # (g) session data toujours None après le reset, outcome inchangé.
    assert await connection.get_session_id(dongle.id) is None
    stored = await dongle_repo.get_by_id(dongle.id)
    assert stored.state == DongleState.ONLINE
    assert connectivity.probe_calls >= 3  # re-sondage borné jusqu'au verdict OK


@pytest.mark.asyncio
async def test_reset_adb_probe_never_ok_error_stays_online(
    tmp_path, dongle_repo, proxy_repo, proxy_service, fake_reset_lock,
    metrics_snapshot, isolation, routing_allocator, fake_nft,
):
    """(b) Sonde jamais OK : ERROR métier (IP non vérifiée), le dongle reste ONLINE."""
    events: list[tuple[str, str]] = []
    connectivity = FakeConnectivity()  # jamais d'IP publique
    connection, adb, nm = make_connection(
        tmp_path, dongle_repo, routing_allocator, isolation, fake_nft, connectivity, events
    )
    await nm.ensure_profile(IFACE, PROFILE)
    dongle = await make_phone(dongle_repo)

    svc = make_reset(dongle_repo, proxy_repo, connection, proxy_service, fake_reset_lock, metrics_snapshot)
    result = await svc.reset(dongle.id)

    assert result.status == "ERROR"
    assert "IP publique non vérifiée" in (result.message or "")
    stored = await dongle_repo.get_by_id(dongle.id)
    assert stored.state == DongleState.ONLINE
    assert stored.last_error is None  # le dongle n'est PAS marqué ERROR


@pytest.mark.asyncio
async def test_reset_adb_pending_when_reset_already_running(
    dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock,
):
    """(c) Un reset déjà en cours (verrou tenu) renvoie exactement PENDING."""
    dongle = await make_phone(dongle_repo)
    fake_reset_lock.acquire(str(dongle.id))
    svc = make_reset(dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock)

    result = await svc.reset(dongle.id)

    assert result.status == "PENDING"


@pytest.mark.asyncio
async def test_reset_adb_post_done_window_pending_and_refusal_does_not_rearm(
    tmp_path, dongle_repo, proxy_repo, proxy_service, fake_reset_lock,
    metrics_snapshot, isolation, routing_allocator, fake_nft, fake_connectivity,
):
    """(d) Après un DONE : PENDING dans la fenêtre de répit ; un refus (état interdit)
    ne ré-horode pas la fenêtre (elle expire toujours 15 s après le DONE)."""
    events: list[tuple[str, str]] = []
    connection, adb, nm = make_connection(
        tmp_path, dongle_repo, routing_allocator, isolation, fake_nft, fake_connectivity, events
    )
    fake_connectivity.set_public_ip("10.64.0.1")
    await nm.ensure_profile(IFACE, PROFILE)
    phone = await make_phone(dongle_repo)
    other = await make_phone(dongle_repo, state=DongleState.DISCOVERED, serial="DEF456")
    clock = ControllableClock()
    svc = make_reset(
        dongle_repo, proxy_repo, connection, proxy_service, fake_reset_lock,
        metrics_snapshot, done_window=ResetPostDoneWindow(seconds=15.0, clock=clock),
    )

    assert (await svc.reset(phone.id)).status == "DONE"
    second = await svc.reset(phone.id)
    assert second.status == "PENDING"
    assert "fenêtre de répit" in (second.message or "")

    clock.t = 7.0
    refused = await svc.reset(other.id)
    assert refused.status == "ERROR"  # refus par état interdit, pas par la fenêtre
    clock.t = 16.0
    # Si le refus ré-horodait, la fenêtre expirerait à 7 + 15 = 22, pas à 15.
    assert (await svc.reset(phone.id)).status == "DONE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", [DongleState.DISCOVERED, DongleState.UNENROLLED, DongleState.CONFIGURING]
)
async def test_reset_adb_forbidden_state_returns_clean_error(
    dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock, state,
):
    """(e) État non récupérable : ERROR + message métier propre, sans jargon technique."""
    dongle = await make_phone(dongle_repo, state=state)
    svc = make_reset(dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock)

    result = await svc.reset(dongle.id)

    assert result.status == "ERROR"
    assert result.message == f"reset impossible dans l'état {state.value}"
    assert "InvalidStateTransition" not in (result.message or "")


@pytest.mark.asyncio
async def test_reset_adb_lock_released_on_timeout(
    tmp_path, dongle_repo, proxy_repo, proxy_service, fake_reset_lock,
    isolation, routing_allocator, fake_nft, fake_connectivity,
):
    """(f) Timeout : ERROR « reset expiré » et le verrou est libéré (re-acquire possible)."""
    events: list[tuple[str, str]] = []
    connection, adb, nm = make_connection(
        tmp_path, dongle_repo, routing_allocator, isolation, fake_nft, fake_connectivity, events
    )
    await nm.ensure_profile(IFACE, PROFILE)
    dongle = await make_phone(dongle_repo)

    async def blocking_reset(dongle_id):
        await asyncio.sleep(3600)  # ne rend jamais la main

    svc = make_reset(
        dongle_repo, proxy_repo, connection, proxy_service, fake_reset_lock, reset_timeout=0.05
    )
    with patch.object(connection, "reset_modem", new=AsyncMock(side_effect=blocking_reset)):
        result = await svc.reset(dongle.id)

    assert result.status == "ERROR"
    assert "reset expiré" in (result.message or "")
    token = fake_reset_lock.acquire(str(dongle.id))  # verrou bien libéré
    fake_reset_lock.release(str(dongle.id), token)


@pytest.mark.asyncio
async def test_reset_adb_lock_released_on_exception(
    tmp_path, dongle_repo, proxy_repo, proxy_service, fake_reset_lock,
    isolation, routing_allocator, fake_nft, fake_connectivity,
):
    """(f) Exception pendant le reset : ERROR + détail, et le verrou est libéré."""
    events: list[tuple[str, str]] = []
    connection, adb, nm = make_connection(
        tmp_path, dongle_repo, routing_allocator, isolation, fake_nft, fake_connectivity, events
    )
    await nm.ensure_profile(IFACE, PROFILE)
    dongle = await make_phone(dongle_repo)

    svc = make_reset(dongle_repo, proxy_repo, connection, proxy_service, fake_reset_lock)
    with patch.object(connection, "reset_modem", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await svc.reset(dongle.id)

    assert result.status == "ERROR"
    assert "RuntimeError: boom" in (result.message or "")
    assert not fake_reset_lock.is_locked(str(dongle.id))


@pytest.mark.asyncio
async def test_diagnostics_adb_phone_online_no_modem(
    tmp_path, dongle_repo, routing_allocator, isolation, fake_nft,
):
    """Téléphone ONLINE : signal None (pas de modem), ping / IP publique épinglés à
    l'interface persistée ; les sondes ne lèvent jamais d'exception."""
    events: list[tuple[str, str]] = []
    connectivity = FakeConnectivity()
    connectivity.set_public_ip("10.64.0.1")
    connection, adb, nm = make_connection(
        tmp_path, dongle_repo, routing_allocator, isolation, fake_nft, connectivity, events
    )
    dongle = await make_phone(dongle_repo)

    metrics = await connection.get_metrics(dongle.id)

    assert metrics.signal_quality is None  # pas de modem ModemManager pour un téléphone
    assert metrics.ping is not None
    assert metrics.ping.packets_received == 4
    assert BEARER_IP in connectivity.interface_calls  # sondes épinglées à l'IP source du bearer
    assert metrics.public_ip == "10.64.0.1"
    assert metrics.public_ip_verdict is ProbeVerdict.OK

    assert await connection.get_public_ip(dongle.id) == "10.64.0.1"
    verdict = await connection.get_public_ip_verdict(dongle.id)
    assert verdict.verdict is ProbeVerdict.OK
    assert verdict.ip == "10.64.0.1"


@pytest.mark.asyncio
async def test_reset_adb_only_identity_survives_repository_round_trip(
    tmp_path, dongle_repo, proxy_repo, proxy_service, fake_reset_lock,
    metrics_snapshot, isolation, routing_allocator, fake_nft,
):
    """Téléphone ADB-only (sans usb_serial, cas SM-A217F sans root) : connect puis
    reset traversent l'aller-retour dépôt. Avant le correctif, la lecture
    renvoyait une identité vide et ``primary_key`` levait ValueError."""
    serial = "R58N729DZMV"
    profile = f"broxy4g-phone-{serial}"
    events: list[tuple[str, str]] = []
    connectivity = FakeConnectivity()
    connectivity.set_public_ip("203.0.113.9")
    connection, adb, nm = make_connection(
        tmp_path, dongle_repo, routing_allocator, isolation, fake_nft, connectivity, events,
        serial=serial,
    )
    await nm.ensure_profile(IFACE, profile)
    phone = await make_phone(dongle_repo, state=DongleState.CONFIGURING, serial=serial, adb_only=True)

    # L'identité ADB-only survit à la lecture de la base (le défaut corrigé).
    reloaded = await dongle_repo.get_by_id(phone.id)
    assert reloaded is not None
    assert reloaded.identity.adb_serial == serial
    assert reloaded.identity_key == serial

    # Connect réel depuis la ligne dépôt, puis reset : DONE, cycle radio au serial ADB.
    connected = await connection.connect(phone.id)
    assert connected.state == DongleState.ONLINE

    svc = make_reset(dongle_repo, proxy_repo, connection, proxy_service, fake_reset_lock, metrics_snapshot)
    result = await svc.reset(phone.id)
    assert result.status == "DONE"
    assert ("airplane", serial) in events

    stored = await dongle_repo.get_by_id(phone.id)
    assert stored.state == DongleState.ONLINE
    assert stored.identity.adb_serial == serial
