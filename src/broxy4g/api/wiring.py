"""Construction du graphe de dépendances de l'API (services sur app.state).

Ne fait que câbler les services entre eux à partir des collaborateurs déjà
résolus (répertoires et adaptateurs) ; l'ordre d'assignation est contractuel,
en particulier : uplink smartphone d'abord, puis les routes uplink qui le
reçoivent, puis la connexion smartphone, puis la connexion modem.
"""
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI

from broxy4g.adapters.mbpi_source import MbpiApnSource
from broxy4g.domain.apn_resolution import ApnResolver
from broxy4g.domain.post_done_window import ResetPostDoneWindow
from broxy4g.services.allowed_hosts import AllowedHostsService
from broxy4g.services.connection import ConnectionService
from broxy4g.services.discovery import DiscoveryService
from broxy4g.services.enrollment import EnrollmentService
from broxy4g.services.local_connections import LocalConnectionsService
from broxy4g.services.network_isolation import NetworkIsolationService
from broxy4g.services.proxy_files import ProxyFileWriter, ProxyPaths
from broxy4g.services.proxy_health import ProxyHealthService
from broxy4g.services.proxy_lifecycle import ProxyLifecycleService
from broxy4g.services.proxy_listen import ProxyListenService
from broxy4g.services.proxy_network import ProxyNetworkService
from broxy4g.services.proxy_port_reservations import ProxyPortReservations
from broxy4g.services.proxy_ports import PortAllocator
from broxy4g.services.proxy_provisioning import ProxyProvisioningService
from broxy4g.services.proxy_secret_box import ProxySecretBox
from broxy4g.services.reset import ResetService
from broxy4g.services.reset_throttle import ResetThrottle
from broxy4g.services.routing_allocator import RoutingAllocator
from broxy4g.services.smartphone_connection import SmartphoneConnectionService
from broxy4g.services.smartphone_uplink import SmartphoneUplinkService
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


def _env_float(name: str, default: float) -> float:
    """Lit une valeur float depuis l'environnement ; défaut si absente/invalid."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def build_service_graph(app: FastAPI, data_dir: str | None) -> None:
    """Peuple app.state avec les services, dans l'ordre de dépendance exact."""
    app.state.isolation = NetworkIsolationService(
        app.state.proxy_repo, app.state.dongle_repo, app.state.nft
    )
    app.state.routing_allocator = RoutingAllocator(
        app.state.dongle_repo, app.state.proxy_repo
    )
    # Stack smartphone (transport ADB, partage USB) : l'uplink d'abord — c'est
    # lui que reçoit le service de routes uplink — puis le cycle de connexion
    # qui l'orchestre, enfin la connexion modem qui y délègue par transport.
    app.state.smartphone_uplink = SmartphoneUplinkService(
        adb=app.state.adb,
        nm=app.state.nm,
        iface=app.state.iface,
        routing=app.state.routing,
    )
    app.state.uplink_routes_service = UplinkRouteService(
        app.state.modem,
        app.state.routing,
        smartphone=app.state.smartphone_uplink,
    )
    app.state.smartphone_connection = SmartphoneConnectionService(
        app.state.dongle_repo,
        app.state.smartphone_uplink,
        app.state.iface,
        app.state.routing,
        app.state.routing_allocator,
        app.state.uplink_routes_service,
        isolation=app.state.isolation,
    )
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
        smartphone=app.state.smartphone_connection,
    )
    files = ProxyFileWriter(ProxyPaths(Path(_resolve_data_dir(data_dir))))
    # Réseau proxy partagé : créé/géré par l'application (pas par Compose),
    # et ses IP/ports publiés sont réservés hors de la base à l'allocation.
    app.state.proxy_network = ProxyNetworkService(
        app.state.docker_network, app.state.iface
    )
    port_allocator = PortAllocator(
        app.state.proxy_repo,
        reservations=ProxyPortReservations(app.state.docker_network, app.state.docker_compose),
    )
    app.state.proxy_listen_service = ProxyListenService(
        app.state.settings_repo, app.state.local_uplink
    )
    app.state.proxy_health_service = ProxyHealthService(
        app.state.proxy_repo, app.state.docker_compose, app.state.proxy_listen_service
    )
    # Coffre de secret : clé issue de l'env (BROXY4G_PROXY_KEY), sinon fichier
    # 0600 du répertoire de la base (stable entre redémarrages). Fail-closed
    # si indisponible.
    app.state.proxy_secret_box = ProxySecretBox(key_path=app.state.proxy_secret_key_path)
    app.state.provisioning = ProxyProvisioningService(
        app.state.proxy_repo,
        app.state.dongle_repo,
        app.state.allowed_host_repo,
        app.state.docker_compose,
        port_allocator,
        files,
        listen=app.state.proxy_listen_service,
        network=app.state.proxy_network,
        secret_box=app.state.proxy_secret_box,
    )
    app.state.proxy_service = ProxyLifecycleService(
        app.state.proxy_repo,
        app.state.provisioning,
        isolation=app.state.isolation,
        routing=app.state.routing,
        secret_box=app.state.proxy_secret_box,
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
        proxy_health=app.state.proxy_health_service,
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
        app.state.modem,
        app.state.dongle_repo,
        apn_source=app.state.apn_source,
        adb=app.state.adb,
    )
    app.state.reset_service = ResetService(
        app.state.dongle_repo,
        app.state.proxy_repo,
        app.state.connection_service,
        app.state.proxy_service,
        app.state.reset_lock,
        metrics_snapshot=app.state.metrics_snapshot,
        throttle=ResetThrottle.from_env(),
        reset_timeout=_env_float("BROXY4G_RESET_TIMEOUT_SECONDS", 300.0),
        done_window=ResetPostDoneWindow(seconds=_env_float("BROXY4G_RESET_POST_DONE_SECONDS", 15.0)),
    )
