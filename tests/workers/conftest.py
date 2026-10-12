import pytest

from broxy4g.adapters.connectivity import FakeConnectivity
from broxy4g.adapters.docker_compose import FakeDockerCompose
from broxy4g.adapters.docker_network import FakeDockerNetwork
from broxy4g.adapters.local_uplink import FakeLocalUplink
from broxy4g.adapters.modem_manager import FakeModemManager
from broxy4g.adapters.net_interface import FakeInterface
from broxy4g.adapters.nftables import FakeNftables
from broxy4g.adapters.routing import FakeRouting
from broxy4g.adapters.usb_device import FakeUsbDevice
from broxy4g.domain.reset_states import ResetLock
from broxy4g.repositories.allowed_host_repository import AllowedHostRepository
from broxy4g.repositories.database import create_engine, create_session_factory, init_db
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.repositories.settings_repository import SettingsRepository
from broxy4g.services.connection import ConnectionService
from broxy4g.services.enrollment import EnrollmentService
from broxy4g.services.local_connections import LocalConnectionsService
from broxy4g.services.metrics_snapshot import MetricsSnapshot
from broxy4g.services.network_isolation import NetworkIsolationService
from broxy4g.services.proxy_files import ProxyFileWriter, ProxyPaths
from broxy4g.services.proxy_lifecycle import ProxyLifecycleService
from broxy4g.services.proxy_network import ProxyNetworkService
from broxy4g.services.proxy_ports import PortAllocator
from broxy4g.services.proxy_provisioning import ProxyProvisioningService
from broxy4g.services.reset import ResetService
from broxy4g.services.routing_allocator import RoutingAllocator
from broxy4g.services.routing_slot_migration import RoutingSlotMigration
from broxy4g.services.uplink_routes import UplinkRouteService
from broxy4g.services.usb_recovery import UsbRecoveryService


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return create_session_factory(engine)


@pytest.fixture
def dongle_repo(session_factory):
    return DongleRepository(session_factory)


@pytest.fixture
def proxy_repo(session_factory):
    return ProxyRepository(session_factory)


@pytest.fixture
def allowed_host_repo(session_factory):
    return AllowedHostRepository(session_factory)


@pytest.fixture
def settings_repo(session_factory):
    return SettingsRepository(session_factory)


@pytest.fixture
def fake_docker_compose():
    return FakeDockerCompose()


@pytest.fixture
def fake_local_uplink():
    return FakeLocalUplink()


@pytest.fixture
def fake_docker_network():
    return FakeDockerNetwork()


@pytest.fixture
def proxy_network_service(fake_docker_network, fake_iface):
    return ProxyNetworkService(fake_docker_network, fake_iface)


@pytest.fixture
def fake_modem():
    return FakeModemManager()


@pytest.fixture
def fake_iface():
    return FakeInterface()


@pytest.fixture
def fake_routing():
    return FakeRouting()


@pytest.fixture
def fake_nft():
    return FakeNftables()


@pytest.fixture
def fake_connectivity():
    return FakeConnectivity()


@pytest.fixture
def fake_reset_lock():
    return ResetLock()


@pytest.fixture
def metrics_snapshot():
    return MetricsSnapshot()


@pytest.fixture
def isolation(proxy_repo, dongle_repo, fake_nft):
    return NetworkIsolationService(proxy_repo, dongle_repo, fake_nft)


@pytest.fixture
def routing_allocator(dongle_repo, proxy_repo):
    return RoutingAllocator(dongle_repo, proxy_repo)


@pytest.fixture
def uplink_routes(fake_modem, fake_routing):
    return UplinkRouteService(fake_modem, fake_routing)


@pytest.fixture
def fake_usb_device():
    return FakeUsbDevice()


@pytest.fixture
def usb_recovery(dongle_repo, uplink_routes, fake_usb_device, fake_reset_lock):
    # Timeout court : les tests n'attendent pas le délai par défaut (~10 s).
    return UsbRecoveryService(
        dongle_repo,
        uplink_routes,
        fake_usb_device,
        fake_reset_lock,
        reappearance_timeout=0.2,
        poll_delay=0.02,
    )


@pytest.fixture
def slot_migration(dongle_repo, proxy_repo):
    return RoutingSlotMigration(dongle_repo, proxy_repo)


@pytest.fixture
def connection_service(
    dongle_repo, fake_modem, fake_iface, fake_routing, fake_nft, fake_connectivity,
    routing_allocator, uplink_routes, isolation,
):
    return ConnectionService(
        dongle_repo,
        fake_modem,
        fake_iface,
        fake_routing,
        fake_nft,
        fake_connectivity,
        routing_allocator,
        uplink_routes,
        isolation=isolation,
    )


@pytest.fixture
def proxy_files(tmp_path):
    return ProxyFileWriter(ProxyPaths(tmp_path))


@pytest.fixture
def port_allocator(proxy_repo):
    return PortAllocator(proxy_repo)


@pytest.fixture
def provisioning(
    proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose, port_allocator, proxy_files
):
    return ProxyProvisioningService(
        proxy_repo,
        dongle_repo,
        allowed_host_repo,
        fake_docker_compose,
        port_allocator,
        proxy_files,
    )


@pytest.fixture
def proxy_service(proxy_repo, provisioning, isolation):
    return ProxyLifecycleService(proxy_repo, provisioning, isolation=isolation)


@pytest.fixture
def enrollment_service(dongle_repo, connection_service, proxy_service, settings_repo):
    return EnrollmentService(
        dongle_repo, connection_service, proxy_service, settings_repo=settings_repo
    )


@pytest.fixture
def reset_service(dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock):
    return ResetService(dongle_repo, proxy_repo, connection_service, proxy_service, fake_reset_lock)


@pytest.fixture
def local_connections(
    fake_local_uplink, proxy_repo, proxy_service, fake_routing, dongle_repo,
    routing_allocator, isolation,
):
    return LocalConnectionsService(
        fake_local_uplink,
        proxy_repo,
        proxy_service,
        fake_routing,
        dongle_repo,
        routing_allocator,
        isolation,
    )


@pytest.fixture
def discovery_worker(fake_modem, dongle_repo):
    from broxy4g.workers.discovery import DiscoveryWorker
    return DiscoveryWorker(fake_modem, dongle_repo)


@pytest.fixture
def reconciliation_worker(
    dongle_repo, proxy_repo, fake_modem, fake_routing, fake_nft,
    fake_connectivity, connection_service, provisioning, fake_docker_compose, metrics_snapshot,
    uplink_routes, slot_migration, isolation, fake_reset_lock, local_connections,
    usb_recovery, proxy_network_service,
):
    from broxy4g.workers.reconciliation import ReconciliationWorker
    return ReconciliationWorker(
        dongle_repo=dongle_repo,
        proxy_repo=proxy_repo,
        modem=fake_modem,
        routing=fake_routing,
        nft=fake_nft,
        connectivity=fake_connectivity,
        connection_service=connection_service,
        provisioning=provisioning,
        docker_compose=fake_docker_compose,
        metrics_snapshot=metrics_snapshot,
        uplink_routes=uplink_routes,
        slot_migration=slot_migration,
        isolation=isolation,
        reset_lock=fake_reset_lock,
        local_connections=local_connections,
        usb_recovery=usb_recovery,
        proxy_network=proxy_network_service,
    )
