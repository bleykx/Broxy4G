from fastapi import Depends, Request

from broxy4g.api.auth import verify_basic_auth
from broxy4g.repositories.settings_repository import SettingsRepository
from broxy4g.services.allowed_hosts import AllowedHostsService
from broxy4g.services.connection import ConnectionService
from broxy4g.services.discovery import DiscoveryService
from broxy4g.services.enrollment import EnrollmentService
from broxy4g.services.local_connections import LocalConnectionsService
from broxy4g.services.metrics_snapshot import MetricsSnapshot
from broxy4g.services.proxy_lifecycle import ProxyLifecycleService
from broxy4g.services.proxy_listen import ProxyListenService
from broxy4g.services.reset import ResetService


def get_enrollment_service(request: Request) -> EnrollmentService:
    return request.app.state.enrollment_service


def get_discovery_service(request: Request) -> DiscoveryService:
    return request.app.state.discovery_service


def get_connection_service(request: Request) -> ConnectionService:
    return request.app.state.connection_service


def get_proxy_service(request: Request) -> ProxyLifecycleService:
    return request.app.state.proxy_service


def get_reset_service(request: Request) -> ResetService:
    return request.app.state.reset_service


def get_allowed_hosts_service(request: Request) -> AllowedHostsService:
    return request.app.state.allowed_hosts_service


def get_local_connections_service(request: Request) -> LocalConnectionsService:
    return request.app.state.local_connections_service


def get_settings_repo(request: Request) -> SettingsRepository:
    return request.app.state.settings_repo


def get_proxy_listen_service(request: Request) -> ProxyListenService:
    return request.app.state.proxy_listen_service


def get_metrics_snapshot(request: Request) -> MetricsSnapshot:
    return request.app.state.metrics_snapshot


def require_auth(request: Request, _auth: None = Depends(verify_basic_auth)) -> None:
    pass
