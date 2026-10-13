import socket
from uuid import uuid4

from broxy4g.adapters.local_uplink import LocalInterface
from broxy4g.domain.proxy import Proxy
from broxy4g.services.proxy_health import HEALTHY, UNHEALTHY, UNKNOWN, ProxyHealthService
from broxy4g.services.proxy_listen import ProxyListenService

DONGLE_ID = uuid4()
CONTAINER = "broxy4g-proxy-test"


def _free_port() -> int:
    """Port éphémère libre sur 127.0.0.1 (garanti sans listener au moment du bind)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _make_proxy(proxy_repo, *, enabled=True, port=8100) -> Proxy:
    proxy = Proxy(
        dongle_id=DONGLE_ID,
        container_name=CONTAINER,
        listen_port=port,
        enabled=enabled,
    )
    return await proxy_repo.save(proxy)


def _listen_service(settings_repo, fake_local_uplink) -> ProxyListenService:
    return ProxyListenService(settings_repo, fake_local_uplink)


async def test_no_proxy_is_unknown(proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo):
    service = ProxyHealthService(proxy_repo, fake_docker_compose, _listen_service(settings_repo, fake_local_uplink))
    health = await service.for_dongle(uuid4())
    assert health.status == UNKNOWN
    assert health.reason == "aucun proxy"


async def test_disabled_proxy_is_unknown(proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo):
    await _make_proxy(proxy_repo, enabled=False)
    service = ProxyHealthService(proxy_repo, fake_docker_compose, _listen_service(settings_repo, fake_local_uplink))
    health = await service.for_dongle(DONGLE_ID)
    assert health.status == UNKNOWN
    assert health.reason == "proxy désactivé"


async def test_for_proxy_disabled_is_unknown(proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo):
    proxy = await _make_proxy(proxy_repo, enabled=False)
    service = ProxyHealthService(proxy_repo, fake_docker_compose, _listen_service(settings_repo, fake_local_uplink))
    health = await service.for_proxy(proxy)
    assert health.status == UNKNOWN
    assert health.reason == "proxy désactivé"


async def test_for_proxy_stopped_container_is_unhealthy(proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo):
    proxy = await _make_proxy(proxy_repo)
    fake_docker_compose.set_state(CONTAINER, "exited")
    service = ProxyHealthService(proxy_repo, fake_docker_compose, _listen_service(settings_repo, fake_local_uplink))
    health = await service.for_proxy(proxy)
    assert health.status == UNHEALTHY
    assert "conteneur" in health.reason


async def test_for_proxy_healthy_when_running_and_reachable(
    proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo
):
    # Cas uplink local : la santé est calculée sur le proxy persisté, sans dongle.
    proxy = await _make_proxy(proxy_repo)
    fake_docker_compose.set_state(CONTAINER, "running")
    listen = _listen_service(settings_repo, fake_local_uplink)

    async def _reachable(host, port):
        return True

    health = await ProxyHealthService(proxy_repo, fake_docker_compose, listen, probe=_reachable).for_proxy(proxy)
    assert health.status == HEALTHY
    assert health.reason is None


async def test_for_dongle_delegates_to_for_proxy(proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo):
    proxy = await _make_proxy(proxy_repo)
    service = ProxyHealthService(proxy_repo, fake_docker_compose, _listen_service(settings_repo, fake_local_uplink))
    via_dongle = await service.for_dongle(DONGLE_ID)
    via_proxy = await service.for_proxy(proxy)
    assert via_dongle == via_proxy


async def test_stopped_container_is_unhealthy(proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo):
    await _make_proxy(proxy_repo)
    fake_docker_compose.set_state(CONTAINER, "exited")
    service = ProxyHealthService(proxy_repo, fake_docker_compose, _listen_service(settings_repo, fake_local_uplink))
    health = await service.for_dongle(DONGLE_ID)
    assert health.status == UNHEALTHY
    assert "conteneur" in health.reason
    assert "exited" in health.reason


async def test_absent_container_is_unhealthy(proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo):
    await _make_proxy(proxy_repo)
    service = ProxyHealthService(proxy_repo, fake_docker_compose, _listen_service(settings_repo, fake_local_uplink))
    health = await service.for_dongle(DONGLE_ID)
    assert health.status == UNHEALTHY
    assert "conteneur" in health.reason
    assert "absent" in health.reason


async def test_unpublished_port_is_unhealthy(proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo):
    await _make_proxy(proxy_repo)
    fake_docker_compose.set_state(CONTAINER, "running")
    # Interface d'écoute configurée mais absente/sans IPv4 → bind None.
    listen = _listen_service(settings_repo, fake_local_uplink)
    fake_local_uplink.set_interfaces([LocalInterface(name="eth0", ip="192.168.1.10", kind="ethernet")])
    await listen.set_interface("eth0")
    fake_local_uplink.set_interfaces([])
    service = ProxyHealthService(proxy_repo, fake_docker_compose, listen)
    health = await service.for_dongle(DONGLE_ID)
    assert health.status == UNHEALTHY
    assert "non publié" in health.reason


async def test_unreachable_port_is_unhealthy_with_probe(proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo):
    await _make_proxy(proxy_repo)
    fake_docker_compose.set_state(CONTAINER, "running")
    listen = _listen_service(settings_repo, fake_local_uplink)
    health = await ProxyHealthService(proxy_repo, fake_docker_compose, listen, probe=_unreachable).for_dongle(DONGLE_ID)
    assert health.status == UNHEALTHY
    assert "non joignable" in health.reason


async def test_healthy_when_running_published_and_reachable(proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo):
    await _make_proxy(proxy_repo)
    fake_docker_compose.set_state(CONTAINER, "running")
    listen = _listen_service(settings_repo, fake_local_uplink)
    fake_local_uplink.set_interfaces([LocalInterface(name="eth0", ip="192.168.1.10", kind="ethernet")])
    await listen.set_interface("eth0")
    seen = []

    async def _reachable(host, port):
        seen.append((host, port))
        return True

    health = await ProxyHealthService(proxy_repo, fake_docker_compose, listen, probe=_reachable).for_dongle(DONGLE_ID)
    assert health.status == HEALTHY
    assert health.reason is None
    assert seen == [("192.168.1.10", 8100)]


async def test_probe_targets_lan_interface_when_bind_is_wildcard(
    proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo
):
    # Bind 0.0.0.0 (aucune interface d'écoute configurée) : la sonde vise
    # l'IP LAN (première ethernet avec IPv4), pas 127.0.0.1.
    await _make_proxy(proxy_repo)
    fake_docker_compose.set_state(CONTAINER, "running")
    fake_local_uplink.set_interfaces([LocalInterface(name="eth0", ip="192.168.1.10", kind="ethernet")])
    listen = _listen_service(settings_repo, fake_local_uplink)
    seen = []

    async def _reachable(host, port):
        seen.append((host, port))
        return True

    health = await ProxyHealthService(proxy_repo, fake_docker_compose, listen, probe=_reachable).for_dongle(DONGLE_ID)
    assert health.status == HEALTHY
    assert seen == [("192.168.1.10", 8100)]


async def test_probe_prefers_configured_listen_interface(
    proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo
):
    # Interface d'écoute configurée : sa cible prime sur la première ethernet.
    await _make_proxy(proxy_repo)
    fake_docker_compose.set_state(CONTAINER, "running")
    fake_local_uplink.set_interfaces(
        [
            LocalInterface(name="eth0", ip="192.168.1.10", kind="ethernet"),
            LocalInterface(name="eth1", ip="10.0.0.5", kind="ethernet"),
        ]
    )
    listen = _listen_service(settings_repo, fake_local_uplink)
    await listen.set_interface("eth1")
    seen = []

    async def _reachable(host, port):
        seen.append((host, port))
        return True

    health = await ProxyHealthService(proxy_repo, fake_docker_compose, listen, probe=_reachable).for_dongle(DONGLE_ID)
    assert health.status == HEALTHY
    assert seen == [("10.0.0.5", 8100)]


async def test_probe_falls_back_to_loopback_without_lan_interface(
    proxy_repo, fake_docker_compose, fake_local_uplink, settings_repo
):
    # Bind 0.0.0.0 sans aucune interface LAN exploitable : repli 127.0.0.1.
    await _make_proxy(proxy_repo)
    fake_docker_compose.set_state(CONTAINER, "running")
    fake_local_uplink.set_interfaces([])
    listen = _listen_service(settings_repo, fake_local_uplink)
    seen = []

    async def _reachable(host, port):
        seen.append((host, port))
        return True

    health = await ProxyHealthService(proxy_repo, fake_docker_compose, listen, probe=_reachable).for_dongle(DONGLE_ID)
    assert health.status == HEALTHY
    assert seen == [("127.0.0.1", 8100)]


async def test_reachable_with_real_local_socket(proxy_repo, fake_docker_compose):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        port = server.getsockname()[1]
        await _make_proxy(proxy_repo, port=port)
        fake_docker_compose.set_state(CONTAINER, "running")
        # Sans service d'écoute : bind par défaut 0.0.0.0 → probe sur 127.0.0.1.
        service = ProxyHealthService(proxy_repo, fake_docker_compose)
        health = await service.for_dongle(DONGLE_ID)
        assert health.status == HEALTHY
    finally:
        server.close()


async def test_default_probe_reports_unreachable_on_closed_port(proxy_repo, fake_docker_compose):
    proxy = await _make_proxy(proxy_repo, port=_free_port())
    fake_docker_compose.set_state(CONTAINER, "running")
    service = ProxyHealthService(proxy_repo, fake_docker_compose)
    health = await service.for_dongle(DONGLE_ID)
    assert health.status == UNHEALTHY
    assert f"port publié non joignable depuis le LAN (127.0.0.1:{proxy.listen_port})" in health.reason


async def test_reachable_false_when_no_listener():
    service = ProxyHealthService(None, None)
    assert await service._reachable("127.0.0.1", 1) is False


async def test_reachable_true_on_real_listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        service = ProxyHealthService(None, None)
        assert await service._reachable("127.0.0.1", server.getsockname()[1]) is True
    finally:
        server.close()


async def _unreachable(host: str, port: int) -> bool:
    return False
