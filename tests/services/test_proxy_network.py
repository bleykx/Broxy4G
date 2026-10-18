"""Tests pour ProxyNetworkService (réparation de la passerelle du bridge proxy)."""

from broxy4g.adapters.docker_network import DockerNetworkInfo, FakeDockerNetwork
from broxy4g.adapters.net_interface import FakeInterface
from broxy4g.services.proxy_network import ProxyNetworkService

BRIDGE = "br-37221a02879e"
OTHER_IFACE = "eth0"


class RecordingInterface(FakeInterface):
    """FakeInterface qui enregistre les écritures d'adresse (pas le fake partagé)."""

    def __init__(self):
        super().__init__()
        self.assignments: list[tuple[str, str, int]] = []

    async def assign_address(self, name: str, address: str, prefix: int) -> None:
        self.assignments.append((name, address, prefix))
        await super().assign_address(name, address, prefix)


def make_service(network: FakeDockerNetwork, iface: RecordingInterface):
    return ProxyNetworkService(network, iface), iface, network


def make_network(with_bridge: bool = True) -> FakeDockerNetwork:
    network = FakeDockerNetwork()
    if with_bridge:
        network.set_network(
            DockerNetworkInfo(
                name="broxy4g-net",
                id="37221a02879eabcd1234",
                driver="bridge",
                bridge_device=BRIDGE,
                subnet="172.28.0.0/16",
                gateway="172.28.0.1",
            )
        )
    return network


async def test_missing_gateway_is_repaired():
    """Bridge sans IP : la passerelle est ré-affirmée (set_up + assign_address)."""
    service, iface, _ = make_service(make_network(), RecordingInterface())

    repaired = await service.ensure_gateway()

    assert repaired is True
    assert iface.assignments == [(BRIDGE, "172.28.0.1", 16)]
    assert BRIDGE in iface.up
    assert await iface.get_ip(BRIDGE) == "172.28.0.1"


async def test_gateway_already_present_no_write():
    """Bridge déjà correct : retour False et aucune écriture système."""
    iface = RecordingInterface()
    iface.set_address(BRIDGE, "172.28.0.1")
    service, _, _ = make_service(make_network(), iface)

    repaired = await service.ensure_gateway()

    assert repaired is False
    assert iface.assignments == []


async def test_absent_network_returns_false_without_write_or_exception():
    """Réseau Docker absent (premier démarrage) : pas d'écriture, pas d'exception."""
    iface = RecordingInterface()
    service, _, network = make_service(make_network(with_bridge=False), iface)

    repaired = await service.ensure_gateway()

    assert repaired is False
    assert iface.assignments == []
    assert network.calls == ["broxy4g-net"]


async def test_ensure_gateway_is_idempotent():
    """Deux appels successifs : une seule écriture, False au second appel."""
    iface = RecordingInterface()
    service, _, _ = make_service(make_network(), iface)

    first = await service.ensure_gateway()
    second = await service.ensure_gateway()

    assert first is True
    assert second is False
    assert len(iface.assignments) == 1


async def test_service_never_touches_other_interfaces():
    """Le service n'écrit que sur le bridge du réseau proxy, jamais sur eth0."""
    iface = RecordingInterface()
    iface.set_address(OTHER_IFACE, "192.168.1.10")  # interface qui doit rester intacte
    service, _, _ = make_service(make_network(), iface)

    await service.ensure_gateway()

    written_devices = {dev for dev, _, _ in iface.assignments}
    assert written_devices == {BRIDGE}
    assert await iface.get_ip(OTHER_IFACE) == "192.168.1.10"


async def test_ensure_ready_creates_absent_network_once():
    """Réseau absent (reboot) : création unique ; le 2e appel est idempotent."""
    iface = RecordingInterface()
    service, _, network = make_service(make_network(with_bridge=False), iface)

    first = await service.ensure_ready()
    second = await service.ensure_ready()

    assert first is True
    assert network.created == ["broxy4g-net"]
    # Le 2e appel voit le réseau présent : aucune seconde création.
    assert second in (True, False)
    assert len(iface.assignments) <= 1


async def test_ensure_ready_absent_network_uses_subnet_and_gateway():
    """La création porte le sous-réseau et la passerelle du domaine."""
    iface = RecordingInterface()
    service, _, network = make_service(make_network(with_bridge=False), iface)

    await service.ensure_ready()

    info = await network.inspect("broxy4g-net")
    assert info is not None
    assert info.subnet == "172.28.0.0/16"
    assert info.gateway == "172.28.0.1"


async def test_ensure_ready_present_network_no_creation():
    """Réseau présent et passerelle en place : aucune création, aucune écriture."""
    iface = RecordingInterface()
    iface.set_address(BRIDGE, "172.28.0.1")
    service, _, network = make_service(make_network(), iface)

    result = await service.ensure_ready()

    assert result is False
    assert network.created == []
    assert iface.assignments == []


async def test_ensure_ready_repairs_lost_gateway_keeps_existing_network():
    """Passerelle perdue : réparation SANS suppression/recréation du pont."""
    iface = RecordingInterface()
    service, _, network = make_service(make_network(), iface)

    repaired = await service.ensure_ready()

    assert repaired is True
    assert network.created == []  # l'existant est conservé
    assert iface.assignments == [(BRIDGE, "172.28.0.1", 16)]
