"""ReconciliationWorker : comportement ADB (téléphone) jusqu'ici non testé.

Couvre :
- la récupération douce d'un dongle ADB : ``adb.wait_for_device`` borné avec le
  serial du téléphone, jamais de ré-énumération USB ;
- le dongle MODEM qui passe toujours par ``usb_recovery.attempt``
  (la branche est scope-transport) ;
- le ré-épinglage du netdev RNDIS renommé dans sysfs (persistance + routes
  reposées sur le NOUVEAU nom) ;
- l'absence de reconnexion d'un téléphone ONLINE (seules les routes sont
  réparées) ;
- la robustesse : interface introuvable (plancher), serial ADB absent
  (repli sur la clé d'identité), adaptateur ``adb=None`` ;
- la vérification de connectivité sans signal (None) sur un téléphone, selon
  l'arbitre tri-état.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from broxy4g.adapters.adb import AdbDevice, FakeAdb
from broxy4g.adapters.connectivity import PingResult
from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo
from broxy4g.adapters.net_interface import FakeInterface
from broxy4g.adapters.network_manager import FakeNetworkManager, NmIpv4
from broxy4g.adapters.routing import FakeRouting
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.transport import Transport
from broxy4g.services.connection import ConnectionService
from broxy4g.services.metrics_snapshot import SummaryMetrics
from broxy4g.services.smartphone_connection import SmartphoneConnectionService
from broxy4g.services.smartphone_uplink import SmartphoneUplinkService
from broxy4g.services.uplink_routes import UplinkRouteService
from broxy4g.workers.reconciliation import ReconciliationWorker

SERIAL = "ABC123"
USB_SERIAL = "PHONE-1"
IFACE = "enx02360c7c147a"
NEW_IFACE = "enxdeadbeef0001"
PROFILE = f"broxy4g-phone-{SERIAL}"
BEARER_IP = "192.168.172.78"
GATEWAY = "192.168.172.245"
TABLE, FWMARK = 10001, 1
MODEM_PATH = "wwan0"
USB_DEVICE = "/sys/devices/pci0000:00/0000:00:14.0/usb1/1-1"


@pytest.fixture
def deps(
    dongle_repo, proxy_repo, fake_modem, fake_routing, fake_nft,
    fake_connectivity, fake_iface, routing_allocator, slot_migration,
    isolation, fake_reset_lock, local_connections, usb_recovery,
    provisioning, fake_docker_compose, metrics_snapshot, proxy_network_service,
):
    """Fakes partagés + fakes ADB (téléphone visible, bail DHCP sur le RNDIS)."""
    fake_adb = FakeAdb()
    fake_adb.devices = [AdbDevice(serial=SERIAL, state="device")]
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4(BEARER_IP, 24)
    nm.gateways[PROFILE] = GATEWAY
    return SimpleNamespace(
        dongle_repo=dongle_repo, proxy_repo=proxy_repo, fake_modem=fake_modem,
        fake_routing=fake_routing, fake_nft=fake_nft,
        fake_connectivity=fake_connectivity, fake_iface=fake_iface,
        routing_allocator=routing_allocator, slot_migration=slot_migration,
        isolation=isolation, fake_reset_lock=fake_reset_lock,
        local_connections=local_connections, usb_recovery=usb_recovery,
        provisioning=provisioning, fake_docker_compose=fake_docker_compose,
        metrics_snapshot=metrics_snapshot, proxy_network_service=proxy_network_service,
        fake_adb=fake_adb, nm=nm,
    )


def make_sysfs(tmp_path, serial: str = SERIAL, iface: str = IFACE) -> str:
    """Arbre sysfs minimal : device USB 1-2 → 1-2:1.0/net/<iface> (serial injectable)."""
    device = tmp_path / "sysfs" / "1-2"
    net = device / "1-2:1.0" / "net" / iface
    net.mkdir(parents=True)
    (device / "serial").write_text(f"{serial}\n")
    (net / "address").write_text("02:36:0c:7c:14:7a\n")
    (net / "operstate").write_text("up\n")
    return str(tmp_path / "sysfs")


def build_adb_worker(deps, tmp_path, *, sysfs_serial=SERIAL, sysfs_iface=IFACE, worker_adb: bool = True):
    """Worker câblé pour la branche ADB : vrai SmartphoneUplinkService + service de
    connexion smartphone, le tout sur fakes (ADB, NetworkManager, sysfs en tmp_path)."""
    uplink = SmartphoneUplinkService(
        deps.fake_adb, deps.nm, FakeInterface(), FakeRouting(),
        sysfs_root=make_sysfs(tmp_path, sysfs_serial, sysfs_iface),
        iface_wait_attempts=2, iface_wait_delay=0.0,
        dhcp_wait_attempts=2, dhcp_wait_delay=0.0,
    )
    uplink_routes = UplinkRouteService(deps.fake_modem, deps.fake_routing, smartphone=uplink)
    smartphone_conn = SmartphoneConnectionService(
        deps.dongle_repo, uplink, deps.fake_iface, deps.fake_routing,
        deps.routing_allocator, UplinkRouteService(deps.fake_modem, deps.fake_routing),
        isolation=deps.isolation, ip_wait_attempts=3, ip_wait_delay=0.0,
    )
    connection_service = ConnectionService(
        deps.dongle_repo, deps.fake_modem, deps.fake_iface, deps.fake_routing,
        deps.fake_nft, deps.fake_connectivity, deps.routing_allocator, uplink_routes,
        isolation=deps.isolation,
        ip_wait_attempts=2, ip_wait_delay=0.0,
        modem_wait_attempts=2, modem_wait_delay=0.0,
        smartphone=smartphone_conn,
    )
    return ReconciliationWorker(
        dongle_repo=deps.dongle_repo,
        proxy_repo=deps.proxy_repo,
        modem=deps.fake_modem,
        routing=deps.fake_routing,
        nft=deps.fake_nft,
        connectivity=deps.fake_connectivity,
        connection_service=connection_service,
        provisioning=deps.provisioning,
        docker_compose=deps.fake_docker_compose,
        metrics_snapshot=deps.metrics_snapshot,
        uplink_routes=uplink_routes,
        slot_migration=deps.slot_migration,
        isolation=deps.isolation,
        reset_lock=deps.fake_reset_lock,
        local_connections=deps.local_connections,
        usb_recovery=deps.usb_recovery,
        proxy_network=deps.proxy_network_service,
        adb=deps.fake_adb if worker_adb else None,
        smartphone=uplink,
    )


async def make_adb_dongle(
    repo, state: DongleState, *, adb_serial: str | None = SERIAL,
    usb_serial: str = USB_SERIAL, interface_name: str | None = IFACE,
) -> Dongle:
    """Téléphone enregistré directement dans le dépôt, avec son slot dédié."""
    dongle = Dongle(
        identity=IdentityMaterial(usb_serial=usb_serial, adb_serial=adb_serial),
        label="Téléphone",
        transport=Transport.ADB,
    )
    dongle.change_state(DongleState.CONFIGURING)
    if state in (DongleState.ONLINE, DongleState.DEGRADED):
        dongle.change_state(DongleState.CONNECTING)
        dongle.change_state(DongleState.ONLINE)
    if state is DongleState.DEGRADED:
        dongle.change_state(DongleState.DEGRADED)
    elif state not in (DongleState.CONFIGURING, DongleState.ONLINE):
        dongle.change_state(state)
    dongle.route_table_id = TABLE
    dongle.fwmark = FWMARK
    dongle.interface_name = interface_name
    return await repo.save(dongle)


def add_modem(fake_modem, device: str | None = None) -> None:
    fake_modem.add_modem(ModemInfo(
        path=MODEM_PATH, imei="123456789012345", iccid=None,
        usb_serial=None, state="connected", interface_name="wwan0",
        device_path=device,
    ))
    fake_modem.set_bearer_ip(MODEM_PATH, BearerIpConfig(address="10.0.0.1", prefix=30, gateway="10.0.0.2"))


async def enroll_modem(enrollment_service) -> Dongle:
    dongle = (await enrollment_service.enroll(
        IdentityMaterial(imei="123456789012345"), "Test", "test.apn",
    )).dongle
    dongle.interface_name = "wwan0"
    dongle.route_table_id = TABLE
    dongle.fwmark = FWMARK
    await enrollment_service.dongle_repo.save(dongle)
    return dongle


async def test_adb_error_soft_recovery_uses_adb_wait_not_usb(deps, tmp_path):
    """(a) ADB en ERROR : ``wait_for_device`` appelé avec le serial du téléphone,
    et ``usb_recovery.attempt`` JAMAIS appelé (pas de ré-énumération USB)."""
    worker = build_adb_worker(deps, tmp_path)
    dongle = await make_adb_dongle(deps.dongle_repo, DongleState.ERROR)
    deps.fake_iface.set_address(IFACE, BEARER_IP)
    deps.fake_connectivity.set_public_ip("5.6.7.8")

    with (
        patch.object(deps.fake_adb, "wait_for_device", new=AsyncMock(return_value=True)) as mock_wait,
        patch.object(deps.usb_recovery, "attempt", new=AsyncMock()) as mock_attempt,
    ):
        await worker.reconcile_dongle(dongle.id)

    mock_wait.assert_awaited_once_with(SERIAL)
    mock_attempt.assert_not_awaited()
    reloaded = await deps.dongle_repo.get_by_id(dongle.id)
    assert reloaded.state is DongleState.ONLINE


async def test_modem_error_still_calls_usb_recovery(
    reconciliation_worker, enrollment_service, fake_modem, fake_connectivity, usb_recovery,
):
    """(b) La branche USB n'est active que pour le transport MODEM."""
    dongle = await enroll_modem(enrollment_service)
    add_modem(fake_modem, device=USB_DEVICE)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.ERROR)
    await reconciliation_worker.dongle_repo.save(dongle)

    with patch.object(usb_recovery, "attempt", new=AsyncMock()) as mock_attempt:
        await reconciliation_worker.reconcile_dongle(dongle.id)

    mock_attempt.assert_awaited_once_with(dongle.id)
    reloaded = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert reloaded.state is DongleState.ONLINE


async def test_adb_interface_renamed_in_sysfs_is_repersisted(deps, tmp_path):
    """(c) Le netdev RNDIS a été renommé dans sysfs : le nouveau nom est persisté
    et les routes/source sont reposées sur le NOUVEAU nom."""
    worker = build_adb_worker(deps, tmp_path, sysfs_iface=NEW_IFACE)
    deps.nm.profile_ips[PROFILE] = NmIpv4(BEARER_IP, 24)  # le profil suit le serial, pas l'interface
    dongle = await make_adb_dongle(deps.dongle_repo, DongleState.ONLINE, interface_name=IFACE)
    deps.fake_iface.set_address(NEW_IFACE, BEARER_IP)
    deps.fake_connectivity.set_public_ip("5.6.7.8")

    await worker.reconcile_dongle(dongle.id)

    reloaded = await deps.dongle_repo.get_by_id(dongle.id)
    assert reloaded.interface_name == NEW_IFACE
    assert reloaded.state is DongleState.ONLINE
    routes = await deps.fake_routing.list_routes(TABLE)
    assert [(r.dest, r.via, r.dev, r.type) for r in routes] == [
        ("default", GATEWAY, NEW_IFACE, "unicast"),
    ]
    assert any(r.source == BEARER_IP for r in await deps.fake_routing.list_rules())


async def test_adb_online_not_reconnected_routes_repaired(deps, tmp_path):
    """(d) Téléphone ONLINE : pas de reconnexion, mais les routes écrasées sont réparées."""
    worker = build_adb_worker(deps, tmp_path)
    dongle = await make_adb_dongle(deps.dongle_repo, DongleState.ONLINE)
    deps.fake_iface.set_address(IFACE, BEARER_IP)
    deps.fake_connectivity.set_public_ip("5.6.7.8")
    # État product : la table du téléphone ne contient qu'une route LAN étrangère.
    await deps.fake_routing.add_route(TABLE, "default", "192.168.1.254", "eth0")

    with patch.object(worker.connection_service, "connect", new=AsyncMock()) as mock_connect:
        await worker.reconcile_dongle(dongle.id)

    mock_connect.assert_not_awaited()
    routes = await deps.fake_routing.list_routes(TABLE)
    assert [(r.dest, r.via, r.dev, r.type) for r in routes] == [
        ("default", GATEWAY, IFACE, "unicast"),
    ]
    assert any(r.source == BEARER_IP for r in await deps.fake_routing.list_rules())
    reloaded = await deps.dongle_repo.get_by_id(dongle.id)
    assert reloaded.state is DongleState.ONLINE


async def test_adb_unresolvable_interface_no_crash_floor_kept(deps, tmp_path):
    """(e) Interface introuvable dans sysfs : le cycle ne casse pas et la table
    dédiée reçoit (reste sur) son plancher anti-fuite."""
    worker = build_adb_worker(deps, tmp_path, sysfs_serial="OTHER")
    dongle = await make_adb_dongle(deps.dongle_repo, DongleState.ERROR)

    await worker.reconcile_dongle(dongle.id)  # ne doit pas lever

    reloaded = await deps.dongle_repo.get_by_id(dongle.id)
    assert reloaded.state is DongleState.ERROR
    routes = await deps.fake_routing.list_routes(TABLE)
    assert [(r.dest, r.type) for r in routes] == [("default", "unreachable")]


async def test_adb_missing_adb_serial_falls_back_to_primary_key(deps, tmp_path):
    """(f) Pas de serial ADB : le repli sur la clé d'identité est gardé (pas
    d'exception), et le ré-épinglement de l'interface utilise ce serial."""
    worker = build_adb_worker(deps, tmp_path, sysfs_serial=USB_SERIAL)
    deps.nm.gateways[f"broxy4g-phone-{USB_SERIAL}"] = GATEWAY
    dongle = await make_adb_dongle(
        deps.dongle_repo, DongleState.ONLINE, adb_serial=None, interface_name=None,
    )
    deps.fake_iface.set_address(IFACE, BEARER_IP)
    deps.fake_connectivity.set_public_ip("5.6.7.8")

    with patch.object(deps.fake_adb, "wait_for_device", new=AsyncMock(return_value=True)) as mock_wait:
        await worker.reconcile_dongle(dongle.id)

    mock_wait.assert_awaited_once_with(USB_SERIAL)  # repli sur la clé d'identité
    reloaded = await deps.dongle_repo.get_by_id(dongle.id)
    assert reloaded.interface_name == IFACE
    assert reloaded.state is DongleState.ONLINE


async def test_adb_adapter_not_configured_does_not_raise(deps, tmp_path):
    """(g) ``adb=None`` (adaptateur non configuré) sur un dongle ADB : pas d'exception."""
    worker = build_adb_worker(deps, tmp_path, worker_adb=False)
    dongle = await make_adb_dongle(deps.dongle_repo, DongleState.ERROR)
    deps.fake_iface.set_address(IFACE, BEARER_IP)
    deps.fake_connectivity.set_public_ip("5.6.7.8")

    await worker.reconcile_dongle(dongle.id)  # ne doit pas lever

    reloaded = await deps.dongle_repo.get_by_id(dongle.id)
    assert reloaded.state is DongleState.ONLINE


async def test_adb_no_signal_public_ip_ok_stays_online(deps, tmp_path):
    """(h1) Téléphone sans signal (``signal_quality is None``) + verdict d'IP
    publique OK : le dongle n'est PAS marqué DEGRADED (il repasse RESTORE_ONLINE)."""
    worker = build_adb_worker(deps, tmp_path)
    dongle = await make_adb_dongle(deps.dongle_repo, DongleState.DEGRADED)
    deps.fake_iface.set_address(IFACE, BEARER_IP)  # bearer vivant → pas de connect()
    deps.fake_connectivity.set_public_ip("5.6.7.8")

    await worker.reconcile_dongle(dongle.id)

    reloaded = await deps.dongle_repo.get_by_id(dongle.id)
    assert reloaded.state is DongleState.ONLINE
    # Pas de modem derrière un téléphone : le signal est None, l'IP publique suffit.
    assert worker.metrics_snapshot.get(dongle.id) == SummaryMetrics(
        public_ip="5.6.7.8", signal_quality=None,
    )


async def test_adb_failed_verdict_degrades_per_arbiter(deps, tmp_path):
    """(h2) Verdict d'IP publique FAILED + ping FAILED (blackhole concordant) :
    l'arbitre tri-état marque DEGRADED, comme pour tout dongle."""
    worker = build_adb_worker(deps, tmp_path)
    dongle = await make_adb_dongle(deps.dongle_repo, DongleState.ONLINE)
    deps.fake_iface.set_address(IFACE, BEARER_IP)  # IP source présente → les sondes tournent
    deps.fake_connectivity.set_ping_result(
        "8.8.8.8",
        PingResult(host="8.8.8.8", packets_sent=4, packets_received=0,
                   min_ms=None, avg_ms=None, max_ms=None),
    )

    await worker.reconcile_dongle(dongle.id)

    reloaded = await deps.dongle_repo.get_by_id(dongle.id)
    assert reloaded.state is DongleState.DEGRADED
