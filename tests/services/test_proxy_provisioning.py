import pytest

from broxy4g.adapters.docker_compose import FakeDockerCompose
from broxy4g.adapters.docker_network import FakeDockerNetwork
from broxy4g.adapters.local_uplink import LocalInterface
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import ProxySource, ProxyState
from broxy4g.services.proxy_files import ProxyFileWriter, ProxyPaths
from broxy4g.services.proxy_listen import ProxyListenService
from broxy4g.services.proxy_materializer import default_host_sources
from broxy4g.services.proxy_port_reservations import ProxyPortReservations
from broxy4g.services.proxy_ports import PortAllocator
from broxy4g.services.proxy_provisioning import ProxyProvisioningService

HOST_SOURCES = ["127.0.0.1", "10.0.0.5"]


@pytest.fixture
def files(tmp_path):
    return ProxyFileWriter(ProxyPaths(tmp_path))


@pytest.fixture
def provisioning(proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose, port_allocator, files):
    return ProxyProvisioningService(
        proxy_repo,
        dongle_repo,
        allowed_host_repo,
        fake_docker_compose,
        port_allocator,
        files,
        host_sources=HOST_SOURCES,
    )


async def _enroll(dongle_repo, imei: str = "123456789012345") -> Dongle:
    dongle = Dongle(identity=IdentityMaterial(imei=imei), label="Test")
    await dongle_repo.save(dongle)
    return dongle


async def test_provision_dongle_creates_full_instance(provisioning, dongle_repo, proxy_repo, fake_docker_compose, files):
    dongle = await _enroll(dongle_repo)

    result = await provisioning.provision_dongle(dongle.id)

    proxy = result.proxy
    assert result.credentials is not None
    assert proxy.state == ProxyState.RUNNING
    assert proxy.listen_port == 31201
    assert proxy.network_ip == "172.28.0.101"
    assert proxy.source_type == ProxySource.DONGLE
    assert proxy.source_ref == "123456789012345"
    assert proxy.auth_user == result.credentials.username
    assert proxy.password_hash == result.credentials.password_hash
    assert proxy.config_path is not None

    cfg = files.paths.config_path(proxy.id).read_text()
    assert f'users "{result.credentials.username}:CR:{result.credentials.password_hash}"' in cfg
    assert f"allow {result.credentials.username} 127.0.0.1,10.0.0.5 * * *" in cfg
    assert "deny *" in cfg

    compose = files.paths.compose_path(proxy.id).read_text()
    assert "0.0.0.0:31201:3128" in compose

    assert fake_docker_compose.projects[proxy.container_name] == "running"
    assert (proxy.container_name, "up") in fake_docker_compose.calls

    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored is not None
    assert stored.listen_port == 31201


async def test_provision_dongle_is_idempotent(provisioning, dongle_repo, proxy_repo, fake_docker_compose):
    dongle = await _enroll(dongle_repo)

    first = await provisioning.provision_dongle(dongle.id)
    second = await provisioning.provision_dongle(dongle.id)

    assert second.proxy.id == first.proxy.id
    assert second.credentials is None
    assert len([c for c in fake_docker_compose.calls if c[1] == "up"]) == 1
    all_proxies = await proxy_repo.list_all()
    assert len(all_proxies) == 1


async def test_provision_dongle_removes_orphan_same_source(
    provisioning, dongle_repo, proxy_repo
):
    """Un proxy orphelin de même source_ref (IMEI) est supprimé avant la création."""
    first = await _enroll(dongle_repo, imei="123456789012345")
    first_result = await provisioning.provision_dongle(first.id)
    orphan_id = first_result.proxy.id
    # Le dongle disparaît mais la ligne proxy est conservée (cas orphelin).
    await dongle_repo.delete(first.id)

    second = await _enroll(dongle_repo, imei="123456789012345")
    result = await provisioning.provision_dongle(second.id)

    assert await proxy_repo.get_by_id(orphan_id) is None
    assert result.proxy.id != orphan_id
    assert result.proxy.dongle_id == second.id
    stored = await proxy_repo.get_by_dongle_id(second.id)
    assert stored is not None
    assert stored.id == result.proxy.id


class _FailingDockerCompose(FakeDockerCompose):
    """Simule un `docker compose` qui échoue au remove (HOME illisible, etc.)."""

    async def remove(self, project: str, compose_dir: str) -> None:
        raise RuntimeError("docker compose remove a échoué")


async def test_remove_cleans_db_and_files_when_docker_fails(
    proxy_repo, dongle_repo, allowed_host_repo, port_allocator, files
):
    """remove() supprime la ligne DB et les fichiers même si Docker lève."""
    provisioning = ProxyProvisioningService(
        proxy_repo,
        dongle_repo,
        allowed_host_repo,
        _FailingDockerCompose(),
        port_allocator,
        files,
        host_sources=HOST_SOURCES,
    )
    dongle = await _enroll(dongle_repo)
    result = await provisioning.provision_dongle(dongle.id)
    proxy_id = result.proxy.id

    await provisioning.remove(proxy_id)

    assert await proxy_repo.get_by_id(proxy_id) is None
    assert not files.paths.proxy_dir(proxy_id).exists()


async def test_provision_dongle_unknown_raises(provisioning):
    from uuid import uuid4

    with pytest.raises(ValueError, match="inconnu"):
        await provisioning.provision_dongle(uuid4())


async def test_provision_disabled_does_not_start(provisioning, dongle_repo, fake_docker_compose, files):
    dongle = await _enroll(dongle_repo)

    result = await provisioning.provision_dongle(dongle.id, enabled=False)

    assert result.proxy.state == ProxyState.STOPPED
    assert result.proxy.enabled is False
    assert fake_docker_compose.calls == []
    assert files.paths.config_path(result.proxy.id).exists()


async def test_provision_local_uses_interface(provisioning, proxy_repo):
    result = await provisioning.provision_local("eth1")

    proxy = result.proxy
    assert proxy.source_type == ProxySource.LOCAL
    assert proxy.source_ref == "eth1"
    assert proxy.interface_name == "eth1"
    assert proxy.dongle_id is None

    again = await provisioning.provision_local("eth1")
    assert again.proxy.id == proxy.id
    assert again.credentials is None


async def test_reprovision_includes_global_and_per_proxy_hosts(
    provisioning, dongle_repo, allowed_host_repo, files
):
    dongle = await _enroll(dongle_repo)
    result = await provisioning.provision_dongle(dongle.id)
    await allowed_host_repo.add_global("203.0.113.0/24")
    await allowed_host_repo.add_for_proxy(result.proxy.id, "198.51.100.7")

    await provisioning.reprovision(result.proxy.id)

    cfg = files.paths.config_path(result.proxy.id).read_text()
    assert "127.0.0.1" in cfg
    assert "10.0.0.5" in cfg
    assert "203.0.113.0/24" in cfg
    assert "198.51.100.7" in cfg


async def _null_hash_proxy(provisioning, dongle_repo, proxy_repo):
    """Provisionne un proxy puis simule une ligne antérieure à la migration
    ``password_hash`` (hash NULL en base)."""
    dongle = await _enroll(dongle_repo)
    result = await provisioning.provision_dongle(dongle.id)
    stored = await proxy_repo.get_by_id(result.proxy.id)
    stored.password_hash = None
    await proxy_repo.save(stored)
    return stored


async def test_reprovision_missing_hash_self_heals(provisioning, dongle_repo, proxy_repo, fake_docker_compose, files):
    proxy = await _null_hash_proxy(provisioning, dongle_repo, proxy_repo)
    up_before = _up_calls(fake_docker_compose)

    repaired = await provisioning.reprovision(proxy.id)

    assert repaired.password_hash
    assert repaired.password_hash.startswith("$1$")
    assert repaired.auth_user
    cfg = files.paths.config_path(proxy.id).read_text()
    assert f'users "{repaired.auth_user}:CR:$1$' in cfg
    assert 'users ":CR:"' not in cfg
    assert _up_calls(fake_docker_compose) == up_before + 1  # un seul `up`
    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored.password_hash == repaired.password_hash


async def test_reprovision_missing_hash_is_idempotent(provisioning, dongle_repo, proxy_repo):
    proxy = await _null_hash_proxy(provisioning, dongle_repo, proxy_repo)

    first = await provisioning.reprovision(proxy.id)
    second = await provisioning.reprovision(proxy.id)

    assert second.password_hash == first.password_hash
    assert second.auth_user == first.auth_user
    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored.password_hash == first.password_hash


async def test_reprovision_missing_hash_disabled_proxy_does_not_start(
    provisioning, dongle_repo, proxy_repo, fake_docker_compose
):
    proxy = await _null_hash_proxy(provisioning, dongle_repo, proxy_repo)
    proxy.enabled = False
    await proxy_repo.save(proxy)
    up_before = _up_calls(fake_docker_compose)

    await provisioning.reprovision(proxy.id)

    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored.password_hash  # réparé et persisté
    assert _up_calls(fake_docker_compose) == up_before  # pas de démarrage


async def test_up_missing_hash_self_heals(provisioning, dongle_repo, proxy_repo, files):
    proxy = await _null_hash_proxy(provisioning, dongle_repo, proxy_repo)

    await provisioning.up(proxy)

    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored.password_hash
    assert stored.password_hash.startswith("$1$")
    cfg = files.paths.config_path(proxy.id).read_text()
    assert f'users "{stored.auth_user}:CR:$1$' in cfg
    assert 'users ":CR:"' not in cfg


async def test_remove_deletes_files_and_record(provisioning, dongle_repo, proxy_repo, fake_docker_compose, files):
    dongle = await _enroll(dongle_repo)
    result = await provisioning.provision_dongle(dongle.id)
    proxy_id = result.proxy.id

    await provisioning.remove(proxy_id)

    assert await proxy_repo.get_by_id(proxy_id) is None
    assert not files.paths.proxy_dir(proxy_id).exists()
    assert (result.proxy.container_name, "remove") in fake_docker_compose.calls


async def test_remove_purges_proxy_allowed_hosts(
    provisioning, dongle_repo, proxy_repo, allowed_host_repo
):
    """remove() purge aussi les IP autorisées du proxy (pas d'orphelins DB)."""
    dongle = await _enroll(dongle_repo)
    result = await provisioning.provision_dongle(dongle.id)
    proxy_id = result.proxy.id
    await allowed_host_repo.add_for_proxy(proxy_id, "198.51.100.7")
    await allowed_host_repo.add_for_proxy(proxy_id, "203.0.113.0/24")
    assert len(await allowed_host_repo.list_for_proxy(proxy_id)) == 2

    await provisioning.remove(proxy_id)

    assert await proxy_repo.get_by_id(proxy_id) is None
    assert await allowed_host_repo.list_for_proxy(proxy_id) == []


def test_default_host_sources_contains_loopback_and_gateway(monkeypatch):
    monkeypatch.delenv("BROXY4G_PROXY_HOST_IPS", raising=False)
    sources = default_host_sources()
    assert "127.0.0.1" in sources
    assert "172.28.0.1" in sources


def test_default_host_sources_reads_env(monkeypatch):
    monkeypatch.setenv("BROXY4G_PROXY_HOST_IPS", "192.168.1.10, 192.168.1.11")
    sources = default_host_sources()
    assert "192.168.1.10" in sources
    assert "192.168.1.11" in sources


def _up_calls(fake_docker_compose) -> int:
    return sum(1 for c in fake_docker_compose.calls if c[1] == "up")


async def _provisioning_with_listen(
    proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose, port_allocator, files,
    settings_repo, fake_local_uplink, listen_name,
):
    fake_local_uplink.set_interfaces(
        [
            LocalInterface(name="eth0", ip="192.168.1.10", kind="ethernet"),
            LocalInterface(name="wlan0", ip="192.168.1.20", kind="wifi"),
        ]
    )
    listen = ProxyListenService(settings_repo, fake_local_uplink)
    if listen_name:
        await listen.set_interface(listen_name)
    service = ProxyProvisioningService(
        proxy_repo,
        dongle_repo,
        allowed_host_repo,
        fake_docker_compose,
        port_allocator,
        files,
        host_sources=HOST_SOURCES,
        listen=listen,
    )
    return service, listen


async def test_provision_compose_uses_listen_bind_ip(
    proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose, port_allocator, files,
    settings_repo, fake_local_uplink,
):
    provisioning, _ = await _provisioning_with_listen(
        proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose,
        port_allocator, files, settings_repo, fake_local_uplink, "eth0",
    )
    dongle = await _enroll(dongle_repo)

    result = await provisioning.provision_dongle(dongle.id)

    compose = files.paths.compose_path(result.proxy.id).read_text()
    assert "192.168.1.10:31201:3128" in compose
    assert "0.0.0.0:31201:3128" not in compose


async def test_ensure_publish_noop_when_compose_unchanged(
    proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose, port_allocator, files,
    settings_repo, fake_local_uplink,
):
    provisioning, _ = await _provisioning_with_listen(
        proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose,
        port_allocator, files, settings_repo, fake_local_uplink, "eth0",
    )
    dongle = await _enroll(dongle_repo)
    result = await provisioning.provision_dongle(dongle.id)
    proxy = result.proxy

    ups_before = _up_calls(fake_docker_compose)
    await provisioning.ensure_publish(proxy)
    ups_after = _up_calls(fake_docker_compose)

    assert ups_after == ups_before  # compose inchangé → pas de `up`


async def test_ensure_publish_ups_when_bind_changes(
    proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose, port_allocator, files,
    settings_repo, fake_local_uplink,
):
    provisioning, listen = await _provisioning_with_listen(
        proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose,
        port_allocator, files, settings_repo, fake_local_uplink, "eth0",
    )
    dongle = await _enroll(dongle_repo)
    result = await provisioning.provision_dongle(dongle.id)
    proxy = result.proxy
    compose = files.paths.compose_path(proxy.id).read_text()
    assert "192.168.1.10:31201:3128" in compose

    # Le bind change (eth0 → wlan0) : le compose désiré diffère du disque.
    await listen.set_interface("wlan0")

    ups_before = _up_calls(fake_docker_compose)
    await provisioning.ensure_publish(proxy)
    ups_after = _up_calls(fake_docker_compose)

    assert ups_after == ups_before + 1
    compose = files.paths.compose_path(proxy.id).read_text()
    assert "192.168.1.20:31201:3128" in compose


class _RecordingNetwork:
    """Double du ProxyNetworkService : enregistre les appels à ensure_ready."""

    def __init__(self):
        self.calls: list[int] = []

    async def ensure_ready(self) -> bool:
        self.calls.append(len(self.calls))
        return False


async def test_provision_skips_ip_held_by_foreign_container(
    proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose, files
):
    """Régression de bout en bout (fakes) : un conteneur étranger détient
    172.28.0.101 (port 31201) → le provisionnement alloue 31202 et le
    ``docker compose up`` réussit au lieu d'échouer sur « Address already
    in use »."""
    fake_docker_network = FakeDockerNetwork()
    fake_docker_network.set_attached_ips("broxy4g-net", {"172.28.0.101"})
    provisioning = ProxyProvisioningService(
        proxy_repo,
        dongle_repo,
        allowed_host_repo,
        fake_docker_compose,
        PortAllocator(
            proxy_repo,
            reservations=ProxyPortReservations(fake_docker_network, fake_docker_compose),
        ),
        files,
        host_sources=HOST_SOURCES,
    )
    dongle = await _enroll(dongle_repo)

    result = await provisioning.provision_dongle(dongle.id)

    assert result.proxy.listen_port == 31202
    assert result.proxy.network_ip == "172.28.0.102"
    assert result.proxy.state == ProxyState.RUNNING
    assert (result.proxy.container_name, "up") in fake_docker_compose.calls


async def test_up_ensures_network_before_compose_up(
    proxy_repo, dongle_repo, allowed_host_repo, fake_docker_compose, files
):
    """up() garantit le réseau partagé (ensure_ready) avant docker compose up."""
    network = _RecordingNetwork()
    provisioning = ProxyProvisioningService(
        proxy_repo,
        dongle_repo,
        allowed_host_repo,
        fake_docker_compose,
        PortAllocator(proxy_repo),
        files,
        host_sources=HOST_SOURCES,
        network=network,
    )
    dongle = await _enroll(dongle_repo)

    result = await provisioning.provision_dongle(dongle.id)

    assert network.calls == [0]  # un seul ensure_ready, avant l'up
    assert (result.proxy.container_name, "up") in fake_docker_compose.calls
