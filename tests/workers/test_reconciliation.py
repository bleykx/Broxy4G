from unittest.mock import AsyncMock, patch

from broxy4g.adapters.local_uplink import LocalInterface
from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import Proxy, ProxySource
from broxy4g.services.metrics_snapshot import SummaryMetrics

MODEM_PATH = "wwan0"
BEARER = BearerIpConfig(address="10.0.0.1", prefix=30, gateway="10.0.0.2")
STALE = "10.95.117.182"


def add_modem(fake_modem, path=MODEM_PATH, imei="123456789012345", interface="wwan0", bearer=BEARER):
    fake_modem.add_modem(ModemInfo(
        path=path, imei=imei, iccid=None,
        usb_serial=None, state="connected", interface_name=interface,
    ))
    fake_modem.set_bearer_ip(path, bearer)


async def _enroll_with_routing(enrollment_service, interface="wwan0"):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = await enrollment_service.enroll(identity, "Test", "test.apn")
    dongle.interface_name = interface
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    await enrollment_service.dongle_repo.save(dongle)
    return dongle


async def test_reconcile_degraded_calls_connect(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    """DEGRADED state should trigger a reconnect attempt."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.DEGRADED)
    await reconciliation_worker.dongle_repo.save(dongle)

    await reconciliation_worker.reconcile_dongle(dongle.id)

    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE


async def test_reconcile_error_calls_connect(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    """ERROR state should trigger a reconnect attempt."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.ERROR)
    await reconciliation_worker.dongle_repo.save(dongle)

    await reconciliation_worker.reconcile_dongle(dongle.id)

    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE


async def test_reconcile_online_skips_connect(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    """ONLINE state should NOT trigger a reconnect."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_iface.set_address("wwan0", "10.0.0.1")

    await reconciliation_worker.reconcile_dongle(dongle.id)

    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE


async def test_reconcile_resetting_with_lock_skips_connect(
    reconciliation_worker, enrollment_service, fake_reset_lock
):
    """RESETTING + verrou tenu = reset réellement en cours → restauration sautée."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = await enrollment_service.enroll(identity, "Test", "test.apn")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.RESETTING)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_reset_lock.acquire(str(dongle.id))  # un reset tourne réellement

    await reconciliation_worker.reconcile_dongle(dongle.id)

    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.RESETTING


async def test_reconcile_resetting_with_lock_fully_ignored(
    reconciliation_worker, enrollment_service, fake_reset_lock
):
    """RESETTING + verrou tenu : connexion, proxy ET connectivité sont totalement ignorés."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = await enrollment_service.enroll(identity, "Test", "test.apn")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.RESETTING)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_reset_lock.acquire(str(dongle.id))  # un reset tourne réellement

    with (
        patch.object(
            reconciliation_worker.connection_service, "connect", new=AsyncMock()
        ) as mock_connect,
        patch.object(
            reconciliation_worker.connection_service, "get_metrics", new=AsyncMock()
        ) as mock_metrics,
        patch.object(
            reconciliation_worker.docker_compose, "state", new=AsyncMock()
        ) as mock_proxy_state,
    ):
        await reconciliation_worker.reconcile_dongle(dongle.id)

    mock_connect.assert_not_awaited()
    mock_metrics.assert_not_awaited()
    mock_proxy_state.assert_not_awaited()


async def test_reconcile_resetting_without_lock_restores(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    """RESETTING SANS verrou = état résiduel post-crash → la connexion est restaurée."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.RESETTING)
    await reconciliation_worker.dongle_repo.save(dongle)

    await reconciliation_worker.reconcile_dongle(dongle.id)

    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE


async def test_reconcile_all_no_dongles(reconciliation_worker, dongle_repo):
    await reconciliation_worker.reconcile_all()


async def test_reconcile_all_repairs_slot_duplicates_before_dongles(
    reconciliation_worker, enrollment_service
):
    """La migration des doublons de slots précède la boucle des dongles."""
    await _enroll_with_routing(enrollment_service)
    calls: list[str] = []

    def record_migration():
        calls.append("migration")

    def record_dongle(_dongle_id):
        calls.append("dongle")

    with (
        patch.object(
            reconciliation_worker.slot_migration,
            "repair_duplicates",
            new=AsyncMock(side_effect=record_migration),
        ),
        patch.object(
            reconciliation_worker,
            "reconcile_dongle",
            new=AsyncMock(side_effect=record_dongle),
        ),
        patch.object(reconciliation_worker, "reconcile_proxies", new=AsyncMock()),
        patch.object(reconciliation_worker, "_reapply_isolation", new=AsyncMock()),
    ):
        await reconciliation_worker.reconcile_all()

    assert calls == ["migration", "dongle"]


async def test_reconcile_all_repairs_duplicate_slot_and_repins_local_proxy(
    reconciliation_worker,
    enrollment_service,
    proxy_repo,
    fake_modem,
    fake_connectivity,
    fake_local_uplink,
    fake_routing,
):
    """End-to-end : le proxy local dupliqué sur le slot du dongle est ré-épinglé ailleurs."""
    dongle = await _enroll_with_routing(enrollment_service)
    proxy = await _seed_local_proxy(proxy_repo, interface="eth0", table=10001, fwmark=1)
    proxy.auth_user = "user"
    proxy.password_hash = "CR:hash"
    await proxy_repo.save(proxy)
    add_modem(fake_modem)
    fake_connectivity.set_public_ip("1.2.3.4")
    fake_local_uplink.set_interfaces([LocalInterface("eth0", "192.168.1.10", "ethernet")])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")

    await reconciliation_worker.reconcile_all()

    reloaded = await proxy_repo.get_by_id(proxy.id)
    assert reloaded.route_table_id is not None
    assert reloaded.route_table_id != dongle.route_table_id
    rules = await fake_routing.list_rules()
    assert any(
        r.table_id == reloaded.route_table_id and r.fwmark == reloaded.fwmark for r in rules
    )
    routes = await fake_routing.list_routes(reloaded.route_table_id)
    assert [(r.dest, r.via, r.dev) for r in routes] == [("default", "192.168.1.1", "eth0")]


async def test_reconcile_proxies_removes_orphan(
    reconciliation_worker, enrollment_service, proxy_repo, dongle_repo
):
    """Un proxy dont le dongle n'existe plus est supprimé par la reconciliation."""
    dongle = await _enroll_with_routing(enrollment_service)
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    assert proxy is not None
    await dongle_repo.delete(dongle.id)

    await reconciliation_worker.reconcile_proxies()

    assert await proxy_repo.get_by_id(proxy.id) is None


async def test_reconcile_proxies_keeps_valid_proxy(
    reconciliation_worker, enrollment_service, proxy_repo
):
    """Un proxy dont le dongle existe toujours est conservé."""
    dongle = await _enroll_with_routing(enrollment_service)
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    assert proxy is not None

    await reconciliation_worker.reconcile_proxies()

    assert await proxy_repo.get_by_id(proxy.id) is not None


async def _seed_local_proxy(proxy_repo, *, interface="eth0", table=10002, fwmark=2):
    proxy = Proxy(
        dongle_id=None,
        source_type=ProxySource.LOCAL,
        source_ref=interface,
        interface_name=interface,
        container_name="broxy4g-local",
        listen_port=31250,
    )
    proxy.network_ip = "172.28.0.150"
    proxy.route_table_id = table
    proxy.fwmark = fwmark
    await proxy_repo.save(proxy)
    return proxy


async def test_reconcile_local_proxy_repins_routing(
    reconciliation_worker, proxy_repo, fake_local_uplink, fake_routing, fake_docker_compose
):
    proxy = await _seed_local_proxy(proxy_repo)
    await fake_docker_compose.up(proxy.container_name, "/tmp/compose")
    fake_local_uplink.set_interfaces([LocalInterface("eth0", "192.168.1.10", "ethernet")])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")

    await reconciliation_worker.reconcile_proxies()

    assert any(r.fwmark == 2 for r in await fake_routing.list_rules())
    routes = await fake_routing.list_routes(10002)
    assert [(r.dest, r.via, r.dev) for r in routes] == [("default", "192.168.1.1", "eth0")]


async def test_reconcile_local_proxy_kept_when_interface_absent(
    reconciliation_worker, proxy_repo, fake_routing
):
    proxy = await _seed_local_proxy(proxy_repo)

    await reconciliation_worker.reconcile_proxies()

    assert await proxy_repo.get_by_id(proxy.id) is not None
    assert (await fake_routing.list_rules()) == []


async def test_reconcile_running_proxy_republishes_when_compose_drifted(
    reconciliation_worker, proxy_repo, fake_docker_compose, proxy_files
):
    """Un proxy déjà running dont le compose sur disque diffère du désiré est re-publié (up)."""
    proxy = Proxy(
        dongle_id=None,
        source_type=ProxySource.DONGLE,
        source_ref="123456789012345",
        container_name="broxy4g-proxy-drift",
        listen_port=31201,
    )
    proxy.network_ip = "172.28.0.101"
    proxy.auth_user = "user"
    proxy.password_hash = "CR:hash"
    await proxy_repo.save(proxy)

    fake_docker_compose.set_state(proxy.container_name, "running")
    # Le compose sur disque a dérivé (bind différent du désiré 0.0.0.0).
    proxy_files.write_compose(proxy.id, "name: drifted\n# stale bind 1.2.3.4\n")

    up_before = sum(1 for c in fake_docker_compose.calls if c[1] == "up")
    await reconciliation_worker._reconcile_proxy(proxy)
    up_after = sum(1 for c in fake_docker_compose.calls if c[1] == "up")

    assert up_after == up_before + 1
    compose = proxy_files.paths.compose_path(proxy.id).read_text()
    assert "0.0.0.0:31201:3128" in compose


async def test_reconcile_dongle_restores_connection(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    fake_connectivity.set_public_ip("1.2.3.4")
    await reconciliation_worker.reconcile_dongle(dongle.id)

    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE


async def test_reconcile_dongle_not_found(reconciliation_worker):
    from uuid import uuid4
    await reconciliation_worker.reconcile_dongle(uuid4())


async def test_reconcile_online_repairs_wrong_route_without_reconnect(
    reconciliation_worker, enrollment_service, fake_modem, fake_routing,
    fake_iface, fake_connectivity,
):
    """ONLINE avec route écrasée : la route est réparée SANS appel à modem.connect()."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_iface.set_address("wwan0", BEARER.address)
    # État product constaté : la table du dongle ne contient que la route du LAN.
    await fake_routing.add_rule(10001, 1)
    await fake_routing.add_route(10001, "default", "192.168.1.254", "eth0")

    with patch.object(fake_modem, "connect", new=AsyncMock()) as mock_connect:
        await reconciliation_worker.reconcile_dongle(dongle.id)

    mock_connect.assert_not_awaited()
    routes = await fake_routing.list_routes(10001)
    assert [(r.dest, r.via, r.dev) for r in routes] == [
        ("default", BEARER.gateway, "wwan0"),
    ]
    assert any(
        r.table_id == 10001 and r.source == BEARER.address
        for r in await fake_routing.list_rules()
    )
    # Le dongle reste ONLINE : pas de reconnexion, pas de changement d'état.
    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE


async def test_reconcile_dongle_degraded_without_public_ip(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_iface.set_address("wwan0", "10.0.0.1")

    # Pas d'IP publique → DEGRADED
    await reconciliation_worker.reconcile_dongle(dongle.id)

    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.DEGRADED


async def test_reconcile_updates_metrics_snapshot(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    """reconcile_dongle should publish public_ip and signal_quality to the snapshot."""
    from broxy4g.adapters.modem_manager import SignalQuality
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    fake_modem.set_signal("wwan0", SignalQuality(percent=85))
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_iface.set_address("wwan0", "10.0.0.1")

    await reconciliation_worker.reconcile_dongle(dongle.id)

    assert reconciliation_worker.metrics_snapshot.get(dongle.id) == SummaryMetrics(
        public_ip="1.2.3.4", signal_quality=85
    )


async def test_degraded_with_stale_address_recovers(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    """Un dongle DEGRADED à cause d'une IPv4 périmée se rétablit après reconciliation."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.DEGRADED)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_iface.set_address("wwan0", STALE)  # adresse résiduelle

    await reconciliation_worker.reconcile_dongle(dongle.id)

    refreshed = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert refreshed.state == DongleState.ONLINE
    # Aucun renommage : l'interface garde le nom du modem (wwan0).
    assert await fake_iface.get_ip("wwan0") == BEARER.address
