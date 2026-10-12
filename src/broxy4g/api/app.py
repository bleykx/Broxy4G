import os
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from broxy4g.adapters.connectivity import ConnectivityAdapter, FakeConnectivity
from broxy4g.adapters.docker_compose import DockerComposeAdapter, FakeDockerCompose
from broxy4g.adapters.local_uplink import FakeLocalUplink, LocalUplinkAdapter
from broxy4g.adapters.mbpi_source import MbpiApnSource
from broxy4g.adapters.modem_manager import FakeModemManager, ModemManagerAdapter
from broxy4g.adapters.net_interface import FakeInterface, InterfaceAdapter
from broxy4g.adapters.nftables import FakeNftables, NftablesAdapter
from broxy4g.adapters.routing import FakeRouting, RoutingAdapter
from broxy4g.api.routers.allowed_hosts import router as allowed_hosts_router
from broxy4g.api.routers.dongles import router as dongles_router
from broxy4g.api.routers.health import router as health_router
from broxy4g.api.routers.local_connections import router as local_connections_router
from broxy4g.api.routers.proxies import router as proxies_router
from broxy4g.api.routers.settings import router as settings_router
from broxy4g.domain.apn_resolution import ApnResolver
from broxy4g.domain.reset_states import ResetLock
from broxy4g.repositories.allowed_host_repository import AllowedHostRepository
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.repositories.settings_repository import SettingsRepository
from broxy4g.services.allowed_hosts import AllowedHostsService
from broxy4g.services.connection import ConnectionService
from broxy4g.services.discovery import DiscoveryService
from broxy4g.services.enrollment import EnrollmentService
from broxy4g.services.local_connections import LocalConnectionsService
from broxy4g.services.metrics_snapshot import MetricsSnapshot
from broxy4g.services.network_isolation import NetworkIsolationService
from broxy4g.services.proxy_files import ProxyFileWriter, ProxyPaths
from broxy4g.services.proxy_health import ProxyHealthService
from broxy4g.services.proxy_lifecycle import ProxyLifecycleService
from broxy4g.services.proxy_listen import ProxyListenService
from broxy4g.services.proxy_ports import PortAllocator
from broxy4g.services.proxy_provisioning import ProxyProvisioningService
from broxy4g.services.reset import ResetService
from broxy4g.services.routing_allocator import RoutingAllocator
from broxy4g.services.uplink_routes import UplinkRouteService


def _build_apn_source() -> ApnResolver | None:
    """Charge la source d'APN MBPI ; retourne None si indisponible."""
    try:
        return MbpiApnSource()
    except Exception:
        return None


def _resolve_data_dir(data_dir: str | None) -> str:
    if data_dir:
        return data_dir
    return os.environ.get("BROXY4G_DATA_DIR") or tempfile.mkdtemp(prefix="broxy4g-")


def _resolve_allowed_host_repo(proxy_repo: ProxyRepository | None, repo):
    if repo is not None:
        return repo
    factory = getattr(proxy_repo, "session_factory", None)
    return AllowedHostRepository(factory) if factory is not None else None


def _resolve_settings_repo(proxy_repo: ProxyRepository | None, repo):
    if repo is not None:
        return repo
    factory = getattr(proxy_repo, "session_factory", None)
    return SettingsRepository(factory) if factory is not None else None


def create_app(
    dongle_repo: DongleRepository | None = None,
    proxy_repo: ProxyRepository | None = None,
    modem: ModemManagerAdapter | None = None,
    iface: InterfaceAdapter | None = None,
    routing: RoutingAdapter | None = None,
    nft: NftablesAdapter | None = None,
    connectivity: ConnectivityAdapter | None = None,
    reset_lock: ResetLock | None = None,
    apn_source: ApnResolver | None = None,
    allowed_host_repo: AllowedHostRepository | None = None,
    settings_repo: SettingsRepository | None = None,
    docker_compose: DockerComposeAdapter | None = None,
    local_uplink: LocalUplinkAdapter | None = None,
    data_dir: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Broxy4G API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    allowed_host_repo = _resolve_allowed_host_repo(proxy_repo, allowed_host_repo)
    settings_repo = _resolve_settings_repo(proxy_repo, settings_repo)

    app.state.dongle_repo = dongle_repo
    app.state.proxy_repo = proxy_repo
    app.state.allowed_host_repo = allowed_host_repo
    app.state.settings_repo = settings_repo
    app.state.modem = modem or FakeModemManager()
    app.state.iface = iface or FakeInterface()
    app.state.routing = routing or FakeRouting()
    app.state.nft = nft or FakeNftables()
    app.state.docker_compose = docker_compose or FakeDockerCompose()
    app.state.local_uplink = local_uplink or FakeLocalUplink()
    app.state.connectivity = connectivity or FakeConnectivity()
    app.state.reset_lock = reset_lock or ResetLock()
    app.state.metrics_snapshot = MetricsSnapshot()
    app.state.apn_source = apn_source if apn_source is not None else _build_apn_source()

    app.state.isolation = NetworkIsolationService(
        app.state.proxy_repo, app.state.dongle_repo, app.state.nft
    )
    app.state.routing_allocator = RoutingAllocator(
        app.state.dongle_repo, app.state.proxy_repo
    )
    app.state.uplink_routes_service = UplinkRouteService(app.state.modem, app.state.routing)
    app.state.connection_service = ConnectionService(
        app.state.dongle_repo,
        app.state.modem,
        app.state.iface,
        app.state.routing,
        app.state.nft,
        app.state.connectivity,
        app.state.routing_allocator,
        uplink_routes=app.state.uplink_routes_service,
        isolation=app.state.isolation,
    )
    files = ProxyFileWriter(ProxyPaths(Path(_resolve_data_dir(data_dir))))
    port_allocator = PortAllocator(app.state.proxy_repo)
    app.state.proxy_listen_service = ProxyListenService(
        app.state.settings_repo, app.state.local_uplink
    )
    app.state.proxy_health_service = ProxyHealthService(
        app.state.proxy_repo, app.state.docker_compose, app.state.proxy_listen_service
    )
    app.state.provisioning = ProxyProvisioningService(
        app.state.proxy_repo,
        app.state.dongle_repo,
        app.state.allowed_host_repo,
        app.state.docker_compose,
        port_allocator,
        files,
        listen=app.state.proxy_listen_service,
    )
    app.state.proxy_service = ProxyLifecycleService(
        app.state.proxy_repo,
        app.state.provisioning,
        isolation=app.state.isolation,
        routing=app.state.routing,
    )
    # Après création du service de cycle de vie : le service d'écoute peut le
    # rappeler (reapply_all_enabled) quand l'interface d'écoute change.
    app.state.proxy_listen_service.applier = app.state.proxy_service
    app.state.allowed_hosts_service = AllowedHostsService(
        app.state.allowed_host_repo, applier=app.state.proxy_service
    )
    app.state.local_connections_service = LocalConnectionsService(
        app.state.local_uplink,
        app.state.proxy_repo,
        app.state.proxy_service,
        app.state.routing,
        app.state.dongle_repo,
        app.state.routing_allocator,
        app.state.isolation,
    )
    app.state.enrollment_service = EnrollmentService(
        app.state.dongle_repo,
        app.state.connection_service,
        app.state.proxy_service,
        modem=app.state.modem,
        apn_source=app.state.apn_source,
        settings_repo=app.state.settings_repo,
    )
    app.state.discovery_service = DiscoveryService(
        app.state.modem, app.state.dongle_repo, apn_source=app.state.apn_source
    )
    app.state.reset_service = ResetService(
        app.state.dongle_repo,
        app.state.proxy_repo,
        app.state.connection_service,
        app.state.proxy_service,
        app.state.reset_lock,
        metrics_snapshot=app.state.metrics_snapshot,
    )

    app.include_router(health_router)
    app.include_router(dongles_router, prefix="/api/v1")
    app.include_router(proxies_router, prefix="/api/v1")
    app.include_router(local_connections_router, prefix="/api/v1")
    app.include_router(allowed_hosts_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")

    return app
