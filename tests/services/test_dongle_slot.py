"""Tests de ``ensure_routing_slot`` : allocation idempotente du slot de routage."""

from broxy4g.adapters.routing import FakeRouting
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.services.dongle_slot import ensure_routing_slot


class CountingRouting(FakeRouting):
    """FakeRouting qui compte les écritures (pour prouver l'absence d'écritures inutiles)."""

    def __init__(self):
        super().__init__()
        self.barrier_writes = 0

    async def add_barrier_route(self, table_id):
        self.barrier_writes += 1
        await super().add_barrier_route(table_id)


class FakeAllocator:
    def __init__(self, table_id: int = 10001, fwmark: int = 7):
        self.allocations = 0
        self.table_id = table_id
        self.fwmark = fwmark

    async def allocate(self):
        self.allocations += 1
        return (self.table_id, self.fwmark)


class FakeRepo:
    def __init__(self):
        self.saved: list[Dongle] = []

    async def save(self, dongle: Dongle) -> Dongle:
        self.saved.append(dongle)
        return dongle


def make_dongle() -> Dongle:
    return Dongle(identity=IdentityMaterial(imei="123456789012345"), label="Test")


async def test_ensure_routing_slot_allocates_persists_and_poses_barrier():
    routing, allocator, repo = CountingRouting(), FakeAllocator(), FakeRepo()
    dongle = make_dongle()

    await ensure_routing_slot(dongle, allocator=allocator, routing=routing, repo=repo)

    assert (dongle.route_table_id, dongle.fwmark) == (10001, 7)
    assert allocator.allocations == 1
    assert repo.saved == [dongle]
    # Plancher anti-fuite posé dès l'allocation.
    assert routing.barrier_writes == 1
    assert [(r.dest, r.type) for r in await routing.list_routes(10001)] == [
        ("default", "unreachable"),
    ]


async def test_ensure_routing_slot_is_idempotent():
    """Un slot déjà alloué : aucun nouvel appel à l'allocateur ni écriture."""
    routing, allocator, repo = CountingRouting(), FakeAllocator(), FakeRepo()
    dongle = make_dongle()
    await ensure_routing_slot(dongle, allocator=allocator, routing=routing, repo=repo)
    writes_before = (allocator.allocations, len(repo.saved), routing.barrier_writes)

    await ensure_routing_slot(dongle, allocator=allocator, routing=routing, repo=repo)

    assert (allocator.allocations, len(repo.saved), routing.barrier_writes) == writes_before
    assert writes_before == (1, 1, 1)


async def test_ensure_routing_slot_no_barrier_when_no_allocation():
    """Dongle déjà doté d'un slot : le plancher n'est pas re-posé (seulement à l'allocation)."""
    routing, allocator, repo = CountingRouting(), FakeAllocator(), FakeRepo()
    dongle = make_dongle()
    dongle.route_table_id = 10001
    dongle.fwmark = 7

    await ensure_routing_slot(dongle, allocator=allocator, routing=routing, repo=repo)

    assert allocator.allocations == 0
    assert repo.saved == []
    assert routing.barrier_writes == 0
