import pytest

from broxy4g.adapters.local_uplink import LocalInterface
from broxy4g.domain.proxy_listen import InvalidListenInterface
from broxy4g.repositories.settings_repository import LISTEN_INTERFACE_KEY
from broxy4g.services.proxy_listen import ProxyListenService


class _CountingApplier:
    def __init__(self):
        self.calls = 0

    async def reapply_all_enabled(self):
        self.calls += 1


def _populate(fake_local_uplink):
    fake_local_uplink.set_interfaces(
        [
            LocalInterface(name="eth0", ip="192.168.1.10", kind="ethernet"),
            LocalInterface(name="wlan0", ip="192.168.1.20", kind="wifi"),
            LocalInterface(name="other0", ip="10.0.0.5", kind="other"),
        ]
    )


def _service(settings_repo, fake_local_uplink, applier=None):
    return ProxyListenService(settings_repo, fake_local_uplink, applier=applier)


async def test_resolve_bind_ip_default_when_unconfigured(settings_repo, fake_local_uplink):
    _populate(fake_local_uplink)
    service = _service(settings_repo, fake_local_uplink)
    assert await service.get_interface() is None
    assert await service.resolve_bind_ip() == "0.0.0.0"


async def test_resolve_bind_ip_returns_interface_ip_when_configured(settings_repo, fake_local_uplink):
    _populate(fake_local_uplink)
    service = _service(settings_repo, fake_local_uplink)
    assert await service.set_interface("wlan0") == "wlan0"
    assert await service.get_interface() == "wlan0"
    assert await service.resolve_bind_ip() == "192.168.1.20"


async def test_resolve_bind_ip_none_when_interface_absent(settings_repo, fake_local_uplink):
    fake_local_uplink.set_interfaces([LocalInterface(name="eth0", ip="192.168.1.10", kind="ethernet")])
    service = _service(settings_repo, fake_local_uplink)
    await service.set_interface("eth0")
    fake_local_uplink.set_interfaces([])  # l'interface disparaît ensuite
    assert await service.get_interface() == "eth0"
    assert await service.resolve_bind_ip() is None


async def test_set_interface_persists_and_calls_applier(settings_repo, fake_local_uplink):
    _populate(fake_local_uplink)
    applier = _CountingApplier()
    service = _service(settings_repo, fake_local_uplink, applier=applier)
    assert await service.set_interface("eth0") == "eth0"
    assert await settings_repo.get(LISTEN_INTERFACE_KEY) == "eth0"
    assert applier.calls == 1


async def test_set_interface_rejects_disallowed_kind(settings_repo, fake_local_uplink):
    _populate(fake_local_uplink)
    service = _service(settings_repo, fake_local_uplink)
    with pytest.raises(InvalidListenInterface):
        await service.set_interface("other0")
    assert await settings_repo.get(LISTEN_INTERFACE_KEY) is None


async def test_set_interface_rejects_unknown_interface(settings_repo, fake_local_uplink):
    _populate(fake_local_uplink)
    service = _service(settings_repo, fake_local_uplink)
    with pytest.raises(InvalidListenInterface):
        await service.set_interface("wwan0")
    assert await settings_repo.get(LISTEN_INTERFACE_KEY) is None


async def test_set_interface_rejects_interface_without_ip(settings_repo, fake_local_uplink):
    fake_local_uplink.set_interfaces([LocalInterface(name="eth1", ip=None, kind="ethernet")])
    service = _service(settings_repo, fake_local_uplink)
    with pytest.raises(InvalidListenInterface):
        await service.set_interface("eth1")


async def test_set_interface_none_clears(settings_repo, fake_local_uplink):
    _populate(fake_local_uplink)
    service = _service(settings_repo, fake_local_uplink)
    await service.set_interface("eth0")
    assert await service.get_interface() == "eth0"
    assert await service.set_interface(None) is None
    assert await settings_repo.get(LISTEN_INTERFACE_KEY) == ""
    assert await service.get_interface() is None
    assert await service.resolve_bind_ip() == "0.0.0.0"
