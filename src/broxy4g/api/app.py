from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from broxy4g.adapters.adb import AdbAdapter, FakeAdb
from broxy4g.adapters.connectivity import ConnectivityAdapter, FakeConnectivity
from broxy4g.adapters.docker_compose import DockerComposeAdapter, FakeDockerCompose
from broxy4g.adapters.docker_network import DockerNetworkAdapter, FakeDockerNetwork
from broxy4g.adapters.local_uplink import FakeLocalUplink, LocalUplinkAdapter
from broxy4g.adapters.modem_manager import FakeModemManager, ModemManagerAdapter
from broxy4g.adapters.net_interface import FakeInterface, InterfaceAdapter
from broxy4g.adapters.network_manager import FakeNetworkManager, NetworkManagerAdapter
from broxy4g.adapters.nftables import FakeNftables, NftablesAdapter
from broxy4g.adapters.routing import FakeRouting, RoutingAdapter
from broxy4g.api.routers.allowed_hosts import router as allowed_hosts_router
from broxy4g.api.routers.dongle_reset import router as dongle_reset_router
from broxy4g.api.routers.dongles import router as dongles_router
from broxy4g.api.routers.health import router as health_router
from broxy4g.api.routers.local_connections import router as local_connections_router
from broxy4g.api.routers.proxies import router as proxies_router
from broxy4g.api.routers.session import router as session_router
from broxy4g.api.routers.settings import router as settings_router
from broxy4g.api.wiring import _build_apn_source, build_service_graph
from broxy4g.domain.apn_resolution import ApnResolver
from broxy4g.domain.reset_states import ResetLock
from broxy4g.repositories.allowed_host_repository import AllowedHostRepository
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.repositories.settings_repository import SettingsRepository
from broxy4g.services.metrics_snapshot import MetricsSnapshot


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
    docker_network: DockerNetworkAdapter | None = None,
    local_uplink: LocalUplinkAdapter | None = None,
    adb: AdbAdapter | None = None,
    nm: NetworkManagerAdapter | None = None,
    data_dir: str | None = None,
    proxy_secret_key_path: Path | None = None,
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
    app.state.docker_network = docker_network or FakeDockerNetwork()
    app.state.local_uplink = local_uplink or FakeLocalUplink()
    app.state.connectivity = connectivity or FakeConnectivity()
    app.state.reset_lock = reset_lock or ResetLock()
    app.state.metrics_snapshot = MetricsSnapshot()
    app.state.apn_source = apn_source if apn_source is not None else _build_apn_source()
    app.state.adb = adb or FakeAdb()
    app.state.nm = nm or FakeNetworkManager()
    app.state.proxy_secret_key_path = proxy_secret_key_path

    build_service_graph(app, data_dir)

    app.include_router(health_router)
    app.include_router(dongles_router, prefix="/api/v1")
    app.include_router(dongle_reset_router, prefix="/api/v1")
    app.include_router(proxies_router, prefix="/api/v1")
    app.include_router(local_connections_router, prefix="/api/v1")
    app.include_router(allowed_hosts_router, prefix="/api/v1")
    app.include_router(session_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")

    return app
