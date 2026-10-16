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


def add_modem(
    fake_modem, path=MODEM_PATH, imei="123456789012345", interface="wwan0",
    bearer=BEARER, device=None,
):
    fake_modem.add_modem(ModemInfo(
        path=path, imei=imei, iccid=None,
        usb_serial=None, state="connected", interface_name=interface,
        device_path=device,
    ))
    fake_modem.set_bearer_ip(path, bearer)


async def _enroll_with_routing(enrollment_service, interface="wwan0"):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
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


async def test_reconcile_degraded_live_bearer_skips_connect(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    """DEGRADED avec bearer vivant : connect() n'est PAS appelé (pas de
    flush_ipv4/assign_address, donc pas de changement d'IP de bearer), et
    l'état repasse à ONLINE si la sonde réussit."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.DEGRADED)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_iface.set_address("wwan0", BEARER.address)  # bearer à jour

    with patch.object(
        reconciliation_worker.connection_service, "connect", new=AsyncMock()
    ) as mock_connect:
        await reconciliation_worker.reconcile_dongle(dongle.id)

    mock_connect.assert_not_awaited()
    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE


async def test_reconcile_degraded_dead_bearer_calls_connect(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    """DEGRADED avec bearer mort (pas d'IP bearer) : connect() est appelé."""
    dongle = await _enroll_with_routing(enrollment_service)
    fake_modem.add_modem(ModemInfo(
        path=MODEM_PATH, imei="123456789012345", iccid=None,
        usb_serial=None, state="connected", interface_name="wwan0", device_path=None,
    ))  # modem présent, mais AUCUNE IP bearer
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.DEGRADED)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_iface.set_address("wwan0", "10.0.0.5")

    with patch.object(
        reconciliation_worker.connection_service, "connect", new=AsyncMock()
    ) as mock_connect:
        await reconciliation_worker.reconcile_dongle(dongle.id)

    mock_connect.assert_awaited_once()
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
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
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
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
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


async def test_reconcile_all_order_proxies_network_isolation(reconciliation_worker):
    """Ordre du cycle : passerelle réseau proxy → isolation → proxies → isolation finale.

    Un conteneur proxy ne démarre que lorsque le réseau Docker et
    l'isolation (droits de sortie, plancher) sont déjà en place ; la
    réapplication finale reste idempotente.
    """
    calls: list[str] = []

    async def record_proxies():
        calls.append("proxies")

    async def record_network():
        calls.append("proxy_network")

    async def record_isolation():
        calls.append("isolation")

    with (
        patch.object(
            reconciliation_worker,
            "reconcile_proxies",
            new=AsyncMock(side_effect=record_proxies),
        ),
        patch.object(
            reconciliation_worker,
            "_ensure_proxy_network",
            new=AsyncMock(side_effect=record_network),
        ),
        patch.object(
            reconciliation_worker,
            "_reapply_isolation",
            new=AsyncMock(side_effect=record_isolation),
        ),
    ):
        await reconciliation_worker.reconcile_all()

    assert calls == ["proxy_network", "isolation", "proxies", "isolation"]


async def test_reconcile_all_proxy_network_failure_does_not_break_cycle(
    reconciliation_worker,
):
    """ensure_gateway qui lève : le cycle se termine et l'isolation est appliquée."""

    async def boom():
        raise RuntimeError("bridge indisponible")

    with (
        patch.object(
            reconciliation_worker.proxy_network,
            "ensure_gateway",
            new=AsyncMock(side_effect=boom),
        ) as mock_ensure,
        patch.object(reconciliation_worker, "_reapply_isolation") as mock_isolation,
    ):
        await reconciliation_worker.reconcile_all()

    mock_ensure.assert_awaited_once()
    # L'isolation est réappliquée avant ET après les proxies (idempotente).
    assert mock_isolation.await_count == 2


async def test_reconcile_dongle_repairs_uplink_routes_before_proxy(
    reconciliation_worker, enrollment_service, fake_modem, fake_iface, fake_connectivity
):
    """La route/plancher de la table est stabilisée AVANT le conteneur du proxy."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    fake_connectivity.set_public_ip("1.2.3.4")
    calls: list[str] = []

    with (
        patch.object(
            reconciliation_worker,
            "_repair_uplink_routes",
            new=AsyncMock(side_effect=lambda _d: calls.append("routes")),
        ),
        patch.object(
            reconciliation_worker.proxy_reconciler,
            "restore_dongle_proxy",
            new=AsyncMock(side_effect=lambda _d: calls.append("proxy")),
        ),
    ):
        await reconciliation_worker.reconcile_dongle(dongle.id)

    assert calls == ["routes", "proxy"]


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
    await reconciliation_worker.proxy_reconciler.reconcile_proxy(proxy)
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
    """Pas d'IP publique (sonde faible, ping OK) → DEGRADED après 2 cycles.

    Test adapté à l'hystérésis asymétrique : l'intention d'origine
    (pas d'IP publique ⇒ DEGRADED) est conservée, mais une sonde faible
    unique ne marque plus DEGRADED (le défaut qui causait les resets en
    boucle sur lien 3G lent).
    """
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem)
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_iface.set_address("wwan0", "10.0.0.1")

    # Cycle 1 : échec faible (curl échoue, ping OK) → l'état est conservé.
    await reconciliation_worker.reconcile_dongle(dongle.id)
    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE

    # Cycle 2 : échec faible consécutif → DEGRADED.
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


USB_DEVICE = "/sys/devices/pci0000:00/0000:00:14.0/usb1/1-1"


async def test_reconcile_error_triggers_usb_recovery(
    reconciliation_worker, enrollment_service, fake_modem,
    fake_connectivity, fake_usb_device,
):
    """Un dongle ERROR avec chemin USB éligible subit la ré-énumération douce."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem, device=USB_DEVICE)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.ERROR)
    await reconciliation_worker.dongle_repo.save(dongle)
    authorized_file = fake_usb_device.register(USB_DEVICE)
    fake_usb_device.on_reenumeration = (
        lambda: fake_modem.simulate_usb_reevaluation(MODEM_PATH)
    )

    await reconciliation_worker.reconcile_dongle(dongle.id)

    assert fake_usb_device.writes == [
        (authorized_file, False), (authorized_file, True),
    ]
    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE


async def test_reconcile_error_without_device_path_skips_usb_recovery(
    reconciliation_worker, enrollment_service, fake_modem,
    fake_connectivity, fake_usb_device,
):
    """Sans chemin USB (ni device_path ni netdev) : la politique est un no-op."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem, device=None)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.ERROR)
    await reconciliation_worker.dongle_repo.save(dongle)

    await reconciliation_worker.reconcile_dongle(dongle.id)

    assert fake_usb_device.writes == []
    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE


async def test_begin_cycle_called_at_reconcile_all_start(
    reconciliation_worker, enrollment_service, usb_recovery
):
    """Le compteur de tentatives USB est remis à zéro au début de reconcile_all."""
    await _enroll_with_routing(enrollment_service)

    with patch.object(usb_recovery, "begin_cycle") as mock_begin:
        await reconciliation_worker.reconcile_all()

    mock_begin.assert_called_once()


async def test_single_usb_attempt_across_reconcile_dongle_calls(
    reconciliation_worker, enrollment_service, fake_modem,
    fake_connectivity, fake_usb_device, usb_recovery,
):
    """Deux reconcile_dongle du même cycle ⇒ une seule tentative USB."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem, device=USB_DEVICE)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.ERROR)
    await reconciliation_worker.dongle_repo.save(dongle)
    fake_usb_device.register(USB_DEVICE)
    fake_usb_device.on_reenumeration = (
        lambda: fake_modem.simulate_usb_reevaluation(MODEM_PATH)
    )

    usb_recovery.begin_cycle()
    await reconciliation_worker.reconcile_dongle(dongle.id)
    await reconciliation_worker.reconcile_dongle(dongle.id)

    assert len(fake_usb_device.writes) == 2  # un seul toggle 0→1


async def test_usb_recovery_failure_does_not_break_cycle(
    reconciliation_worker, enrollment_service, fake_modem,
    fake_connectivity, fake_usb_device, usb_recovery,
):
    """Une exception dans la récupération USB ne casse pas la restauration."""
    dongle = await _enroll_with_routing(enrollment_service)
    add_modem(fake_modem, device=USB_DEVICE)
    fake_connectivity.set_public_ip("1.2.3.4")
    dongle.change_state(DongleState.CONNECTING)
    dongle.change_state(DongleState.ONLINE)
    dongle.change_state(DongleState.ERROR)
    await reconciliation_worker.dongle_repo.save(dongle)

    async def boom(_dongle_id):
        raise RuntimeError("sysfs inaccessible")

    with patch.object(usb_recovery, "attempt", new=AsyncMock(side_effect=boom)):
        await reconciliation_worker.reconcile_dongle(dongle.id)

    dongle = await reconciliation_worker.dongle_repo.get_by_id(dongle.id)
    assert dongle.state == DongleState.ONLINE


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
