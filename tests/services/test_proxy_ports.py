import pytest

from broxy4g.domain.proxy import Proxy, ProxySource
from broxy4g.services.proxy_ports import (
    PORT_RANGE_END,
    PORT_RANGE_START,
    PortAllocator,
    PortRangeExhausted,
    pick_free_port,
)


class FakeProxyRepo:
    def __init__(self):
        self.ports: set[int] = set()

    async def used_ports(self) -> set[int]:
        return set(self.ports)


class FakeReservations:
    def __init__(self, ports: set[int] | None = None):
        self.ports = ports or set()

    async def reserved_ports(self) -> set[int]:
        return set(self.ports)


def test_port_range_constants():
    assert PORT_RANGE_START == 31201
    assert PORT_RANGE_END == 31299


def test_pick_free_port_first_free():
    assert pick_free_port(set()) == PORT_RANGE_START


def test_pick_free_port_fills_hole():
    assert pick_free_port({31201}) == 31202
    assert pick_free_port({31201, 31203}) == 31202
    assert pick_free_port({31201, 31202}) == 31203


def test_pick_free_port_last_port():
    assert pick_free_port(set(range(PORT_RANGE_START, PORT_RANGE_END))) == PORT_RANGE_END


def test_pick_free_port_exhausted():
    with pytest.raises(PortRangeExhausted):
        pick_free_port(set(range(PORT_RANGE_START, PORT_RANGE_END + 1)))


def test_pick_free_port_custom_range():
    assert pick_free_port(set(), start=8080, end=8082) == 8080
    assert pick_free_port({8080}, start=8080, end=8082) == 8081
    with pytest.raises(PortRangeExhausted):
        pick_free_port({8080, 8081, 8082}, start=8080, end=8082)


@pytest.mark.asyncio
async def test_port_allocator_picks_increasing_ports_with_fake_repo():
    fake = FakeProxyRepo()
    allocator = PortAllocator(fake)

    port1 = await allocator.allocate()
    fake.ports.add(port1)
    port2 = await allocator.allocate()
    fake.ports.add(port2)

    assert port1 == PORT_RANGE_START
    assert port2 == PORT_RANGE_START + 1
    assert {port1, port2} == {31201, 31202}


@pytest.mark.asyncio
async def test_port_allocator_exhausted_with_fake_repo():
    fake = FakeProxyRepo()
    fake.ports = set(range(PORT_RANGE_START, PORT_RANGE_END + 1))
    with pytest.raises(PortRangeExhausted):
        await PortAllocator(fake).allocate()


@pytest.mark.asyncio
async def test_port_allocator_skips_ports_reserved_by_docker():
    """Base vide mais 31201-31205 réservés par Docker (conteneurs étrangers)
    → le premier port libre 31206 est choisi."""
    fake = FakeProxyRepo()
    reservations = FakeReservations({31201, 31202, 31203, 31204, 31205})

    port = await PortAllocator(fake, reservations=reservations).allocate()

    assert port == 31206


@pytest.mark.asyncio
async def test_port_allocator_exhausted_when_docker_reserves_whole_range():
    """Plage entièrement réservée par Docker (et vide en base) → PortRangeExhausted."""
    fake = FakeProxyRepo()
    reservations = FakeReservations(set(range(PORT_RANGE_START, PORT_RANGE_END + 1)))

    with pytest.raises(PortRangeExhausted):
        await PortAllocator(fake, reservations=reservations).allocate()


@pytest.mark.asyncio
async def test_port_allocator_union_db_and_docker_reservations():
    """Les réservations Docker et la base sont unies : le trou commun est sauté."""
    fake = FakeProxyRepo()
    fake.ports = {31201, 31203}
    reservations = FakeReservations({31202})

    port = await PortAllocator(fake, reservations=reservations).allocate()

    assert port == 31204


@pytest.mark.asyncio
async def test_port_allocator_with_real_repo(port_allocator, proxy_repo):
    first = await port_allocator.allocate()
    assert first == PORT_RANGE_START

    await proxy_repo.save(
        Proxy(
            dongle_id=None,
            container_name="p1",
            listen_port=first,
            source_type=ProxySource.LOCAL,
            source_ref="local",
        )
    )
    second = await port_allocator.allocate()
    assert second == first + 1
