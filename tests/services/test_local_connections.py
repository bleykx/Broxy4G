import pytest

from broxy4g.adapters.local_uplink import LocalInterface
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import ProxySource
from broxy4g.domain.proxy_isolation import ISOLATION_TABLE
from broxy4g.services.local_connections import LocalConnectionNotFound


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
    # La règle fwmark est retirée, mais la table dédiée conserve son plancher
    # `default unreachable` : elle n'est jamais laissée sans route `default`
    # (sinon le noyau retombe sur la route par défaut de l'hôte = fuite).
    routes = await fake_routing.list_routes(provisioned.proxy.route_table_id)
    assert [(r.dest, r.type) for r in routes] == [("default", "unreachable")]


@pytest.mark.asyncio
async def test_unenroll_unknown_interface_is_noop(local_connections, fake_routing):
    await local_connections.unenroll("eth0")
    assert (await fake_routing.list_rules()) == []


@pytest.mark.asyncio
async def test_slot_allocation_avoids_dongle_tables(local_connections, dongle_repo):
    dongle = Dongle(identity=IdentityMaterial(imei="123456789012345"), label="Test")
    dongle.route_table_id = 10001
    await dongle_repo.save(dongle)

    table_id, fwmark = await local_connections.allocator.allocate()

    assert table_id == 10002
    assert fwmark == table_id - 10000


@pytest.mark.asyncio
async def test_list_all_includes_gateway_and_link_state(local_connections, fake_local_uplink):
    fake_local_uplink.set_interfaces([_iface("eth0")])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")
    fake_local_uplink.set_link_state("eth0", "up")

    connections = await local_connections.list_all()

    assert connections[0].gateway == "192.168.1.1"
    assert connections[0].link_state == "up"


@pytest.mark.asyncio
async def test_list_all_unknown_link_state_when_unreported(local_connections, fake_local_uplink):
    fake_local_uplink.set_interfaces([_iface("eth0")])

    connections = await local_connections.list_all()

    assert connections[0].gateway is None
    assert connections[0].link_state == "unknown"


@pytest.mark.asyncio
async def test_get_returns_detail_without_proxy(local_connections, fake_local_uplink):
    fake_local_uplink.set_interfaces([_iface("eth0")])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")
    fake_local_uplink.set_link_state("eth0", "down")
    fake_local_uplink.set_dns("eth0", ["192.168.1.1", "1.1.1.1"])

    detail = await local_connections.get("eth0")

    assert detail.interface_name == "eth0"
    assert detail.ip == "192.168.1.10"
    assert detail.kind == "ethernet"
    assert detail.gateway == "192.168.1.1"
    assert detail.link_state == "down"
    assert detail.dns == ["192.168.1.1", "1.1.1.1"]
    assert detail.enrolled is False
    assert detail.proxy is None
    assert detail.proxy_health is None


@pytest.mark.asyncio
async def test_get_returns_detail_with_proxy_and_healthy_health(
    local_connections, fake_local_uplink, fake_docker_compose
):
    fake_local_uplink.set_interfaces([_iface("eth0")])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")
    fake_local_uplink.set_dns("eth0", None)
    provisioned = await local_connections.enroll("eth0")
    fake_docker_compose.set_state(provisioned.proxy.container_name, "running")

    detail = await local_connections.get("eth0")

    assert detail.enrolled is True
    assert detail.proxy is not None
    assert detail.proxy.id == provisioned.proxy.id
    assert detail.proxy_health is not None
    assert detail.proxy_health.status == "healthy"
    assert detail.proxy_health.reason is None


@pytest.mark.asyncio
async def test_get_health_unhealthy_when_container_stopped(
    local_connections, fake_local_uplink, fake_docker_compose
):
    fake_local_uplink.set_interfaces([_iface("eth0")])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")
    provisioned = await local_connections.enroll("eth0")
    fake_docker_compose.set_state(provisioned.proxy.container_name, "exited")

    detail = await local_connections.get("eth0")

    assert detail.proxy_health.status == "unhealthy"
    assert "conteneur" in detail.proxy_health.reason


@pytest.mark.asyncio
async def test_get_unknown_interface_raises(local_connections, fake_local_uplink):
    fake_local_uplink.set_interfaces([_iface("eth0")])

    with pytest.raises(LocalConnectionNotFound, match="interface inconnue"):
        await local_connections.get("eth9")
