from pathlib import Path

import pytest

from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import Proxy, ProxySource, ProxyState
from broxy4g.domain.proxy_isolation import ISOLATION_TABLE


async def _seed_dongle(dongle_repo) -> object:
    dongle = Dongle(identity=IdentityMaterial(imei="123456789012345"), label="Test")
    await dongle_repo.save(dongle)
    return dongle.id


async def _seed_online_dongle(dongle_repo) -> object:
    dongle = Dongle(identity=IdentityMaterial(imei="123456789012345"), label="Test")
    dongle.fwmark = 7
    dongle.interface_name = "wwan0"
    dongle.state = DongleState.ONLINE
    await dongle_repo.save(dongle)
    return dongle.id


@pytest.mark.asyncio
async def test_create_creates_disabled_proxy_with_credentials(proxy_service, dongle_repo):
    dongle_id = await _seed_dongle(dongle_repo)
    provisioned = await proxy_service.create(dongle_id, enabled=False)
    proxy = provisioned.proxy
    assert proxy.dongle_id == dongle_id
    assert proxy.source_type == ProxySource.DONGLE
    assert 31201 <= proxy.listen_port <= 31299
    assert proxy.state == ProxyState.STOPPED
    assert proxy.enabled is False
    assert provisioned.credentials is not None
    assert proxy.password_hash


@pytest.mark.asyncio
async def test_create_is_idempotent(proxy_service, dongle_repo):
    dongle_id = await _seed_dongle(dongle_repo)
    first = await proxy_service.create(dongle_id, enabled=False)
    second = await proxy_service.create(dongle_id, enabled=False)
    assert second.proxy.id == first.proxy.id
    assert second.credentials is None


@pytest.mark.asyncio
async def test_start_transitions_to_running(proxy_service, dongle_repo):
    dongle_id = await _seed_dongle(dongle_repo)
    provisioned = await proxy_service.create(dongle_id, enabled=False)
    result = await proxy_service.start(provisioned.proxy.id)
    assert result.state == ProxyState.RUNNING
    assert result.enabled is True


@pytest.mark.asyncio
async def test_stop_transitions_to_stopped(proxy_service, dongle_repo):
    dongle_id = await _seed_dongle(dongle_repo)
    provisioned = await proxy_service.create(dongle_id, enabled=True)
    result = await proxy_service.stop(provisioned.proxy.id)
    assert result.state == ProxyState.STOPPED
    assert result.enabled is False


@pytest.mark.asyncio
async def test_rotate_credentials_changes_user(proxy_service, dongle_repo):
    dongle_id = await _seed_dongle(dongle_repo)
    provisioned = await proxy_service.create(dongle_id, enabled=False)
    previous_user = provisioned.proxy.auth_user
    credentials = await proxy_service.rotate_credentials(provisioned.proxy.id)
    reloaded = await proxy_service.get(provisioned.proxy.id)
    assert credentials.password
    assert reloaded.auth_user == credentials.username
    assert reloaded.auth_user != previous_user


@pytest.mark.asyncio
async def test_remove_deletes_proxy(proxy_service, dongle_repo):
    dongle_id = await _seed_dongle(dongle_repo)
    provisioned = await proxy_service.create(dongle_id, enabled=True)
    await proxy_service.remove(provisioned.proxy.id)
    assert await proxy_service.get_by_dongle_id(dongle_id) is None


@pytest.mark.asyncio
async def test_reapply_enabled_recycles_container_and_rewrites_cfg(
    proxy_service, dongle_repo, proxy_repo, allowed_host_repo, fake_docker_compose
):
    dongle_id = await _seed_dongle(dongle_repo)
    provisioned = await proxy_service.create(dongle_id, enabled=True)
    proxy = provisioned.proxy
    cfg_path = proxy.config_path
    await allowed_host_repo.add_global("203.0.113.0/24")

    await proxy_service.reapply(proxy.id)

    calls = [action for (name, action) in fake_docker_compose.calls if name == proxy.container_name]
    # Le premier "up" vient de la création ; la réapplication ajoute down puis up.
    assert calls.index("down") < calls.index("up", calls.index("down"))
    assert "203.0.113.0/24" in Path(cfg_path).read_text()
    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored.state == ProxyState.RUNNING
    assert stored.enabled is True


@pytest.mark.asyncio
async def test_reapply_disabled_rewrites_cfg_without_container(
    proxy_service, dongle_repo, proxy_repo, allowed_host_repo, fake_docker_compose
):
    dongle_id = await _seed_dongle(dongle_repo)
    provisioned = await proxy_service.create(dongle_id, enabled=False)
    proxy = provisioned.proxy
    cfg_path = proxy.config_path
    await allowed_host_repo.add_global("198.51.100.0/24")

    await proxy_service.reapply(proxy.id)

    calls = [name for (name, action) in fake_docker_compose.calls]
    assert proxy.container_name not in calls
    assert "198.51.100.0/24" in Path(cfg_path).read_text()
    stored = await proxy_repo.get_by_id(proxy.id)
    assert stored.enabled is False
    assert stored.state == ProxyState.STOPPED


@pytest.mark.asyncio
async def test_reapply_all_enabled_skips_disabled(
    proxy_service, dongle_repo, allowed_host_repo, fake_docker_compose
):
    first = Dongle(identity=IdentityMaterial(imei="111111111111111"), label="A")
    second = Dongle(identity=IdentityMaterial(imei="222222222222222"), label="B")
    await dongle_repo.save(first)
    await dongle_repo.save(second)
    enabled = (await proxy_service.create(first.id, enabled=True)).proxy
    disabled = (await proxy_service.create(second.id, enabled=False)).proxy

    await proxy_service.reapply_all_enabled()

    names = {name for (name, _) in fake_docker_compose.calls}
    assert enabled.container_name in names
    assert disabled.container_name not in names
    actions = [action for (name, action) in fake_docker_compose.calls]
    assert "down" in actions and "up" in actions


async def _mark_rules(fake_nft):
    ruleset = await fake_nft.get_ruleset()
    if ISOLATION_TABLE not in ruleset.tables:
        return []
    return ruleset.tables[ISOLATION_TABLE].chains["proxy_mark"].rules


@pytest.mark.asyncio
async def test_create_applies_network_isolation(proxy_service, dongle_repo, fake_nft):
    dongle_id = await _seed_online_dongle(dongle_repo)
    await proxy_service.create(dongle_id, enabled=True)
    # Retour DNAT (accept) + marquage egress du proxy.
    assert len(await _mark_rules(fake_nft)) == 2


@pytest.mark.asyncio
async def test_stop_clears_network_isolation(proxy_service, dongle_repo, fake_nft):
    dongle_id = await _seed_online_dongle(dongle_repo)
    provisioned = await proxy_service.create(dongle_id, enabled=True)
    await proxy_service.stop(provisioned.proxy.id)
    assert await _mark_rules(fake_nft) == []


@pytest.mark.asyncio
async def test_release_pinning_poses_barrier(proxy_service, fake_routing):
    """_release_pinning retire la règle fwmark mais pose un plancher (pas de remove_route)."""
    proxy = Proxy(
        dongle_id=None,
        container_name="local-test",
        listen_port=31201,
        source_type=ProxySource.LOCAL,
        route_table_id=20001,
        fwmark=9,
    )
    # État initial : règle fwmark + route default réelle.
    await fake_routing.add_rule(20001, 9)
    await fake_routing.add_route(20001, "default", "192.168.1.1", "eth0")

    await proxy_service._release_pinning(proxy)

    # La règle fwmark est retirée...
    rules = await fake_routing.list_rules()
    assert not any(r.table_id == 20001 and r.fwmark == 9 for r in rules)
    # ...mais la table garde un plancher default unreachable (aucun remove_route).
    routes = await fake_routing.list_routes(20001)
    assert len(routes) == 1
    assert routes[0].dest == "default"
    assert routes[0].type == "unreachable"
