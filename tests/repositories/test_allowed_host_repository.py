from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_global_add_and_list(allowed_host_repo):
    await allowed_host_repo.add_global("10.0.0.5")
    await allowed_host_repo.add_global("192.168.1.0/24")
    assert await allowed_host_repo.list_global() == ["10.0.0.5", "192.168.1.0/24"]


@pytest.mark.asyncio
async def test_global_add_deduplicated(allowed_host_repo):
    await allowed_host_repo.add_global("10.0.0.5")
    await allowed_host_repo.add_global("10.0.0.5")
    assert await allowed_host_repo.list_global() == ["10.0.0.5"]


@pytest.mark.asyncio
async def test_global_add_normalizes(allowed_host_repo):
    await allowed_host_repo.add_global("192.168.1.5/24")
    assert await allowed_host_repo.list_global() == ["192.168.1.0/24"]
    # Un ajout équivalent (même forme normalisée) ne duplique pas
    await allowed_host_repo.add_global("192.168.1.0/24")
    assert await allowed_host_repo.list_global() == ["192.168.1.0/24"]


@pytest.mark.asyncio
async def test_global_add_invalid_raises(allowed_host_repo):
    with pytest.raises(ValueError):
        await allowed_host_repo.add_global("2001:db8::1")


@pytest.mark.asyncio
async def test_global_remove(allowed_host_repo):
    await allowed_host_repo.add_global("10.0.0.5")
    assert await allowed_host_repo.remove_global("10.0.0.5") is True
    assert await allowed_host_repo.remove_global("10.0.0.5") is False
    assert await allowed_host_repo.list_global() == []


@pytest.mark.asyncio
async def test_global_remove_normalizes(allowed_host_repo):
    await allowed_host_repo.add_global("192.168.1.5/24")
    assert await allowed_host_repo.remove_global("192.168.1.0/24") is True


@pytest.mark.asyncio
async def test_for_proxy_add_list_remove(allowed_host_repo):
    proxy_id = uuid4()
    other_id = uuid4()

    await allowed_host_repo.add_for_proxy(proxy_id, "10.0.0.5")
    await allowed_host_repo.add_for_proxy(proxy_id, "192.168.1.0/24")
    await allowed_host_repo.add_for_proxy(other_id, "172.16.0.0/16")

    assert await allowed_host_repo.list_for_proxy(proxy_id) == ["10.0.0.5", "192.168.1.0/24"]
    assert await allowed_host_repo.list_for_proxy(other_id) == ["172.16.0.0/16"]
    assert await allowed_host_repo.list_for_proxy(uuid4()) == []

    assert await allowed_host_repo.remove_for_proxy(proxy_id, "10.0.0.5") is True
    assert await allowed_host_repo.remove_for_proxy(proxy_id, "10.0.0.5") is False
    assert await allowed_host_repo.list_for_proxy(proxy_id) == ["192.168.1.0/24"]
    assert await allowed_host_repo.list_for_proxy(other_id) == ["172.16.0.0/16"]


@pytest.mark.asyncio
async def test_for_proxy_add_deduplicated(allowed_host_repo):
    proxy_id = uuid4()
    await allowed_host_repo.add_for_proxy(proxy_id, "10.0.0.5")
    await allowed_host_repo.add_for_proxy(proxy_id, "10.0.0.5")
    assert await allowed_host_repo.list_for_proxy(proxy_id) == ["10.0.0.5"]
