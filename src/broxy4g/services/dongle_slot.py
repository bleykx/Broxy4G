"""Attribution idempotente du slot de routage (table dédiée + fwmark) d'un dongle.

Extraite de ``ConnectionService`` pour être partagée entre le chemin modem
et le chemin smartphone (ADB) : une seule implémentation de l'allocation.
"""

from broxy4g.adapters.routing import RoutingAdapter
from broxy4g.domain.dongle import Dongle
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.routing_allocator import RoutingAllocator

__all__ = ["ensure_routing_slot"]


async def ensure_routing_slot(
    dongle: Dongle,
    *,
    allocator: RoutingAllocator,
    routing: RoutingAdapter,
    repo: DongleRepository,
) -> None:
    """Attribue (une fois) table de routage + fwmark au dongle.

    L'allocateur partagé (dongles + proxies) garantit l'unicité du slot.
    """
    if dongle.route_table_id and dongle.fwmark:
        return
    table_id, fwmark = await allocator.allocate()
    dongle.route_table_id = table_id
    dongle.fwmark = fwmark
    await repo.save(dongle)
    # Plancher anti-fuite dès l'allocation (default unreachable).
    await routing.add_barrier_route(table_id)
