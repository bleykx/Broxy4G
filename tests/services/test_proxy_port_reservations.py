"""Tests pour ProxyPortReservations (réservations Docker hors de la base)."""

from broxy4g.adapters.docker_compose import FakeDockerCompose
from broxy4g.adapters.docker_network import FakeDockerNetwork
from broxy4g.services.proxy_port_reservations import ProxyPortReservations


def make(*, attached: set[str] | None = None, published: set[int] | None = None):
    network = FakeDockerNetwork()
    network.set_attached_ips("broxy4g-net", attached or set())
    compose = FakeDockerCompose()
    compose.published_ports = set(published or set())
    return ProxyPortReservations(network, compose)


async def test_empty_docker_state_reserves_nothing():
    assert await make().reserved_ports() == set()


async def test_attached_bridge_ips_map_to_proxy_ports():
    # IP attachées au pont par un conteneur étranger → ports dérivés.
    reservations = make(attached={"172.28.0.101", "172.28.0.103"})
    assert await reservations.reserved_ports() == {31201, 31203}


async def test_non_proxy_ips_on_bridge_are_ignored():
    # Passerelle et IP hors schéma (autre sous-réseau) ne réservent rien.
    reservations = make(attached={"172.28.0.1", "172.28.0.200", "192.168.1.40"})
    assert await reservations.reserved_ports() == set()


async def test_published_ports_filtered_to_proxy_range():
    reservations = make(published={31205, 8099, 31300, 22})
    assert await reservations.reserved_ports() == {31205}


async def test_union_of_attached_ips_and_published_ports():
    reservations = make(attached={"172.28.0.102"}, published={31205, 8099})
    assert await reservations.reserved_ports() == {31202, 31205}
