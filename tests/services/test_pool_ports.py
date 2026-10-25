"""Tests de l'allocation du port d'écoute des pools (plage 31301-31399)."""


import pytest

from broxy4g.domain.proxy import Proxy
from broxy4g.domain.proxy_pool import POOL_PORT_END, POOL_PORT_START, ProxyPool
from broxy4g.services.pool_ports import PoolPortAllocator, PortRangeExhausted


def _allocator(pool_repo, proxy_repo, fake_docker_compose) -> PoolPortAllocator:
    return PoolPortAllocator(pool_repo, proxy_repo, fake_docker_compose)


async def test_allocate_first_free_port_on_empty_base(pool_repo, proxy_repo, fake_docker_compose):
    allocator = _allocator(pool_repo, proxy_repo, fake_docker_compose)
    assert await allocator.allocate() == 31301


async def test_allocate_skips_pool_proxy_and_docker_ports_in_range(
    pool_repo, proxy_repo, fake_docker_compose
):
    await pool_repo.save(ProxyPool(label="a", listen_port=31301))
    await proxy_repo.save(Proxy(dongle_id=None, container_name="px", listen_port=31302))
    fake_docker_compose.published_ports = {31303}
    allocator = _allocator(pool_repo, proxy_repo, fake_docker_compose)
    assert await allocator.allocate() == 31304


async def test_docker_published_port_outside_range_is_ignored(
    pool_repo, proxy_repo, fake_docker_compose
):
    fake_docker_compose.published_ports = {8080}
    allocator = _allocator(pool_repo, proxy_repo, fake_docker_compose)
    assert await allocator.allocate() == 31301


async def test_exhausted_range_raises(pool_repo, proxy_repo, fake_docker_compose):
    # Les 99 ports de la plage sont occupés par des pools persistés.
    for port in range(POOL_PORT_START, POOL_PORT_END + 1):
        await pool_repo.save(ProxyPool(label=f"p{port}", listen_port=port))
    allocator = _allocator(pool_repo, proxy_repo, fake_docker_compose)
    with pytest.raises(PortRangeExhausted):
        await allocator.allocate()


async def test_successive_allocations_differ_when_first_persisted(
    pool_repo, proxy_repo, fake_docker_compose
):
    allocator = _allocator(pool_repo, proxy_repo, fake_docker_compose)
    first = await allocator.allocate()
    await pool_repo.save(ProxyPool(label="a", listen_port=first))
    second = await allocator.allocate()
    assert second == first + 1
