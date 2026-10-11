from uuid import uuid4

from broxy4g.services.allowed_hosts import AllowedHostsService


class FakeApplier:
    """Faux ProxyConfigApplier enregistrant les appels reçus."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.all_calls: int = 0
        self.reapplied: list[str] = []

    async def reapply(self, proxy_id):
        if self.fail:
            raise RuntimeError("réapplication impossible")
        self.reapplied.append(str(proxy_id))

    async def reapply_all_enabled(self):
        if self.fail:
            raise RuntimeError("réapplication impossible")
        self.all_calls += 1


async def test_add_global_reapplies_all_enabled(allowed_host_repo):
    applier = FakeApplier()
    svc = AllowedHostsService(allowed_host_repo, applier=applier)

    result = await svc.add_global("203.0.113.5")

    assert result == "203.0.113.5"
    assert applier.all_calls == 1
    assert applier.reapplied == []


async def test_remove_global_reapplies_all_enabled(allowed_host_repo):
    applier = FakeApplier()
    svc = AllowedHostsService(allowed_host_repo, applier=applier)
    await svc.add_global("203.0.113.5")
    applier.all_calls = 0

    result = await svc.remove_global("203.0.113.5")

    assert result is True
    assert applier.all_calls == 1


async def test_add_for_proxy_reapplies_only_that_proxy(allowed_host_repo):
    applier = FakeApplier()
    svc = AllowedHostsService(allowed_host_repo, applier=applier)
    target = uuid4()
    other = uuid4()

    await svc.add_for_proxy(target, "198.51.100.7")

    assert applier.all_calls == 0
    assert applier.reapplied == [str(target)]
    assert str(other) not in applier.reapplied


async def test_remove_for_proxy_reapplies_only_that_proxy(allowed_host_repo):
    applier = FakeApplier()
    svc = AllowedHostsService(allowed_host_repo, applier=applier)
    target = uuid4()
    await svc.add_for_proxy(target, "198.51.100.7")
    applier.reapplied.clear()

    result = await svc.remove_for_proxy(target, "198.51.100.7")

    assert result is True
    assert applier.reapplied == [str(target)]


async def test_add_global_returns_value_when_applier_fails(allowed_host_repo):
    applier = FakeApplier(fail=True)
    svc = AllowedHostsService(allowed_host_repo, applier=applier)

    result = await svc.add_global("203.0.113.5")

    assert result == "203.0.113.5"
    assert "203.0.113.5" in await svc.list_global()


async def test_remove_for_proxy_returns_value_when_applier_fails(allowed_host_repo):
    applier = FakeApplier(fail=True)
    svc = AllowedHostsService(allowed_host_repo, applier=applier)
    target = uuid4()
    await svc.add_for_proxy(target, "198.51.100.7")

    result = await svc.remove_for_proxy(target, "198.51.100.7")

    assert result is True
    assert await svc.list_for_proxy(target) == []


async def test_mutation_without_applier_is_a_noop(allowed_host_repo):
    svc = AllowedHostsService(allowed_host_repo)

    assert await svc.add_global("203.0.113.5") == "203.0.113.5"
    assert await svc.remove_global("203.0.113.5") is True
    assert await svc.add_for_proxy(uuid4(), "198.51.100.7") == "198.51.100.7"
