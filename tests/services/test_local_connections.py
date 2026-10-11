import pytest

from broxy4g.adapters.local_uplink import LocalInterface
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import ProxySource
from broxy4g.domain.proxy_isolation import ISOLATION_TABLE


def _iface(name="eth0", ip="192.168.1.10", kind="ethernet"):
    return LocalInterface(name=name, ip=ip, kind=kind)


def _mark_rules(ruleset):
    if ISOLATION_TABLE not in ruleset.tables:
        return []
    return ruleset.tables[ISOLATION_TABLE].chains["proxy_mark"].rules


@pytest.mark.asyncio
async def test_list_all_marks_enrolled_interfaces(local_connections, fake_local_uplink):
    fake_local_uplink.set_interfaces([_iface("eth0"), _iface("wlan0", kind="wifi")])

    connections = await local_connections.list_all()

    assert [c.interface_name for c in connections] == ["eth0", "wlan0"]
    assert all(not c.enrolled for c in connections)


@pytest.mark.asyncio
async def test_list_all_excludes_dongle_interfaces(
    local_connections, fake_local_uplink, dongle_repo
):
    dongle = Dongle(identity=IdentityMaterial(imei="123456789012345"), label="Test")
    dongle.interface_name = "wwan0"
    await dongle_repo.save(dongle)
    fake_local_uplink.set_interfaces([_iface("eth0"), _iface("wwan0", kind="other")])

    connections = await local_connections.list_all()

    # L'interface du dongle enrôlé est exclue, même si l'uplink la liste encore
    assert [c.interface_name for c in connections] == ["eth0"]


@pytest.mark.asyncio
async def test_enroll_pins_proxy_to_interface(
    local_connections, fake_local_uplink, fake_routing, proxy_repo
):
    fake_local_uplink.set_interfaces([_iface()])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")

    provisioned = await local_connections.enroll("eth0")
    proxy = provisioned.proxy

    assert proxy.source_type == ProxySource.LOCAL
    assert proxy.interface_name == "eth0"
    assert proxy.route_table_id is not None
    assert proxy.fwmark is not None
    assert any(r.fwmark == proxy.fwmark for r in await fake_routing.list_rules())
    routes = await fake_routing.list_routes(proxy.route_table_id)
    assert [(r.dest, r.via, r.dev) for r in routes] == [("default", "192.168.1.1", "eth0")]


@pytest.mark.asyncio
async def test_enroll_applies_isolation_mark(local_connections, fake_local_uplink, fake_nft):
    fake_local_uplink.set_interfaces([_iface()])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")

    await local_connections.enroll("eth0")

    rules = _mark_rules(await fake_nft.get_ruleset())
    # Retour DNAT (accept) + marquage egress du proxy.
    assert len(rules) == 2
    assert rules[-1].action.startswith("meta mark set 0x")


@pytest.mark.asyncio
async def test_enroll_rejects_unknown_interface(local_connections):
    with pytest.raises(ValueError, match="interface inconnue"):
        await local_connections.enroll("eth9")


@pytest.mark.asyncio
async def test_enroll_rejects_interface_without_gateway(local_connections, fake_local_uplink):
    fake_local_uplink.set_interfaces([_iface()])

    with pytest.raises(ValueError, match="passerelle"):
        await local_connections.enroll("eth0")


@pytest.mark.asyncio
async def test_unenroll_releases_proxy_and_routing(
    local_connections, fake_local_uplink, fake_routing, proxy_repo
):
    fake_local_uplink.set_interfaces([_iface()])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")
    provisioned = await local_connections.enroll("eth0")

    await local_connections.unenroll("eth0")

    assert await proxy_repo.get_by_source(ProxySource.LOCAL, "eth0") is None
    assert (await fake_routing.list_rules()) == []
    assert (await fake_routing.list_routes(provisioned.proxy.route_table_id)) == []


@pytest.mark.asyncio
async def test_unenroll_unknown_interface_is_noop(local_connections, fake_routing):
    await local_connections.unenroll("eth0")
    assert (await fake_routing.list_rules()) == []


@pytest.mark.asyncio
async def test_slot_allocation_avoids_dongle_tables(local_connections, dongle_repo):
    dongle = Dongle(identity=IdentityMaterial(imei="123456789012345"), label="Test")
    dongle.route_table_id = 10001
    await dongle_repo.save(dongle)

    table_id, fwmark = await local_connections._allocate_slot()

    assert table_id == 10002
    assert fwmark == table_id - 10000
