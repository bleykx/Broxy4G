"""Tests du service d'orchestration des pools (gateway factice, aucune I/O système)."""

import asyncio
import itertools
from uuid import uuid4

import pytest

from broxy4g.domain.proxy import Proxy, ProxySource, ProxyState
from broxy4g.domain.proxy_pool import ProxyPool
from broxy4g.services.pool_gateway_port import FakePoolGateway
from broxy4g.services.pool_membership import PoolMembershipService
from broxy4g.services.pool_ports import PoolPortAllocator
from broxy4g.services.pool_service import (
    PoolCredentialsUnavailable,
    PoolMemberInvalid,
    PoolNotFound,
    PoolService,
)
from broxy4g.services.proxy_secret_box import ProxySecretBox


def _make(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
) -> tuple[PoolService, FakePoolGateway]:
    membership = PoolMembershipService(
        pool_repo, proxy_repo, dongle_repo, secret_box, reset_lock=fake_reset_lock
    )
    gateway = FakePoolGateway()
    ports = PoolPortAllocator(pool_repo, proxy_repo, fake_docker_compose)
    service = PoolService(
        pool_repo, proxy_repo, ports, membership, secret_box, gateway, pool_listen
    )
    return service, gateway


_PROXY_PORTS = itertools.count(31210)


async def _eligible_local_proxy(proxy_repo, secret_box) -> Proxy:
    proxy = Proxy(
        dongle_id=None,
        container_name=f"px-{uuid4().hex[:8]}",
        listen_port=next(_PROXY_PORTS),
        state=ProxyState.RUNNING,
        auth_user="alice",
        password_encrypted=secret_box.encrypt("secret"),
        source_type=ProxySource.LOCAL,
        enabled=True,
        network_ip="172.28.0.5",
    )
    return await proxy_repo.save(proxy)


async def test_create_persists_pool_and_returns_password_once(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, gateway = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    pool, password = await svc.create("Prod")
    assert pool.listen_port == 31301
    assert pool.auth_user
    assert pool.password_encrypted
    assert pool.password_encrypted != password
    assert secret_box.decrypt(pool.password_encrypted) == password
    stored = await pool_repo.get(pool.id)
    assert stored is not None and stored.label == "Prod"
    [runtime] = gateway.calls[-1]
    assert runtime.pool_id == pool.id
    assert runtime.listen_port == 31301
    assert runtime.username == pool.auth_user
    assert runtime.password == password


async def test_create_blank_label_rejected(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    with pytest.raises(ValueError):
        await svc.create("   ")
    assert await pool_repo.list_all() == []


async def test_create_fail_closed_without_secret_box(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, fake_reset_lock, pool_listen
):
    """Coffre indisponible (fail-closed) : exception et aucun pool persisté."""
    broken_box = ProxySecretBox(key=None, key_path=None)
    assert broken_box.available is False
    membership = PoolMembershipService(pool_repo, proxy_repo, dongle_repo, broken_box, reset_lock=fake_reset_lock)
    gateway = FakePoolGateway()
    svc = PoolService(pool_repo, proxy_repo, PoolPortAllocator(pool_repo, proxy_repo, fake_docker_compose),
                      membership, broken_box, gateway, pool_listen)
    with pytest.raises(PoolCredentialsUnavailable):
        await svc.create("Prod")
    assert await pool_repo.list_all() == []


async def test_get_unknown_pool(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen):
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    with pytest.raises(PoolNotFound):
        await svc.get(uuid4())


async def test_update_label_and_disable_stops_exposure(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, gateway = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    pool, _ = await svc.create("Prod")
    updated = await svc.update(pool.id, label="Renommé", enabled=False)
    assert updated.label == "Renommé"
    assert updated.enabled is False
    stored = await pool_repo.get(pool.id)
    assert stored.label == "Renommé" and stored.enabled is False
    assert gateway.calls[-1] == []  # désactivé : plus exposé au gateway


async def test_update_unknown_pool(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen):
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    with pytest.raises(PoolNotFound):
        await svc.update(uuid4(), label="x")


async def test_delete_removes_pool_and_members(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    pool, _ = await svc.create("Prod")
    proxy = await _eligible_local_proxy(proxy_repo, secret_box)
    assert await svc.add_member(pool.id, proxy.id) is True
    await svc.delete(pool.id)
    assert await pool_repo.get(pool.id) is None
    assert await pool_repo.list_member_ids(pool.id) == []
    with pytest.raises(PoolNotFound):
        await svc.delete(pool.id)


async def test_add_member_unknown_proxy_rejected(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    pool, _ = await svc.create("Prod")
    with pytest.raises(PoolMemberInvalid):
        await svc.add_member(pool.id, uuid4())


async def test_add_member_idempotent_and_remove(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    pool, _ = await svc.create("Prod")
    proxy = await _eligible_local_proxy(proxy_repo, secret_box)
    assert await svc.add_member(pool.id, proxy.id) is True
    assert await svc.add_member(pool.id, proxy.id) is False
    assert await svc.remove_member(pool.id, proxy.id) is True
    assert await svc.remove_member(pool.id, proxy.id) is False


async def test_credentials_round_trip_and_corruption(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    pool, password = await svc.create("Prod")
    assert await svc.credentials(pool.id) == (pool.auth_user, password)
    pool.password_encrypted = "x"  # valeur corrompue
    await pool_repo.save(pool)
    with pytest.raises(PoolCredentialsUnavailable):
        await svc.credentials(pool.id)


async def test_status_aggregates_members_listen_and_endpoint(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    pool, _ = await svc.create("Prod")
    proxy = await _eligible_local_proxy(proxy_repo, secret_box)
    await svc.add_member(pool.id, proxy.id)
    status = await svc.status(pool.id)
    assert len(status.members) == 1
    assert status.members[0].proxy_id == proxy.id
    assert status.members[0].eligible is True
    assert status.listen.bind_ip == "127.0.0.1"  # statut du gateway factice
    assert status.published_ip == "192.168.1.10"
    assert status.endpoint == "192.168.1.10:31301"


async def test_sync_gateway_only_enabled_pool_with_credentials(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, gateway = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    good, _ = await svc.create("ok")
    disabled = await pool_repo.save(ProxyPool(label="off", listen_port=31302, auth_user="u",
                                              password_encrypted=secret_box.encrypt("p"), enabled=False))
    no_creds = await pool_repo.save(ProxyPool(label="sans", listen_port=31303, auth_user=None,
                                              password_encrypted=None, enabled=True))
    await svc.sync_gateway()
    assert [r.pool_id for r in gateway.calls[-1]] == [good.id]
    assert disabled.id not in {r.pool_id for r in gateway.calls[-1]}
    assert no_creds.id not in {r.pool_id for r in gateway.calls[-1]}


async def test_sync_gateway_pool_without_credentials_is_skipped_without_exception(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, gateway = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    await pool_repo.save(ProxyPool(label="sans", listen_port=31301, auth_user=None,
                                   password_encrypted=None, enabled=True))
    await svc.sync_gateway()  # jamais d'exception
    assert gateway.calls[-1] == []


async def test_prune_members_removes_orphan(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    pool, _ = await svc.create("Prod")
    orphan = await _eligible_local_proxy(proxy_repo, secret_box)
    keeper = await _eligible_local_proxy(proxy_repo, secret_box)
    await svc.add_member(pool.id, orphan.id)
    await svc.add_member(pool.id, keeper.id)
    await proxy_repo.delete(orphan.id)  # proxy supprimé : membre orphelin
    assert await svc.prune_members() == 1
    assert await pool_repo.list_member_ids(pool.id) == [keeper.id]


async def test_prune_members_empty_proxy_base_removes_nothing(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    """Base de proxies vide : rien n'est supprimé (peut venir d'un chargement raté)."""
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    pool = await pool_repo.save(ProxyPool(label="p", listen_port=31301))
    orphan_proxy_id = uuid4()
    await pool_repo.add_member(pool.id, orphan_proxy_id)
    assert await svc.prune_members() == 0
    assert await pool_repo.list_member_ids(pool.id) == [orphan_proxy_id]


async def test_concurrent_creations_get_distinct_ports(
    pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen
):
    """Deux créations simultanées ne partagent jamais le même port d'écoute."""
    svc, _ = _make(pool_repo, proxy_repo, fake_docker_compose, dongle_repo, secret_box, fake_reset_lock, pool_listen)
    first, second = await asyncio.gather(svc.create("A"), svc.create("B"))
    ports = {first[0].listen_port, second[0].listen_port}
    assert ports == {31301, 31302}
    assert len(await pool_repo.list_all()) == 2
