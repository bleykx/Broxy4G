"""UplinkRouteService : branche ADB (téléphone) de ``apply_for_dongle``.

Le bearer vient du partage USB (RNDIS) via ``SmartphoneUplinkService`` ;
le chemin modem reste intact.
"""

import logging

from broxy4g.adapters.adb import FakeAdb
from broxy4g.adapters.modem_manager import FakeModemManager
from broxy4g.adapters.net_interface import FakeInterface
from broxy4g.adapters.network_manager import FakeNetworkManager, NmIpv4
from broxy4g.adapters.routing import FakeRouting, RoutingRoute
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.transport import Transport
from broxy4g.services.smartphone_uplink import SmartphoneUplinkService
from broxy4g.services.uplink_routes import UplinkRouteService

SERIAL = "ABC123"
IFACE = "enx02360c7c147a"
PROFILE = f"broxy4g-phone-{SERIAL}"


class CountingRouting(FakeRouting):
    """FakeRouting qui compte les écritures (pour prouver l'absence d'écritures inutiles)."""

    def __init__(self):
        super().__init__()
        self.barrier_writes = 0

    async def add_barrier_route(self, table_id):
        self.barrier_writes += 1
        await super().add_barrier_route(table_id)


def make_sysfs(tmp_path, serial: str = SERIAL, iface: str = IFACE) -> str:
    """Arbre sysfs minimal : device USB 1-2 → 1-2:1.0/net/<iface>."""
    root = tmp_path / "sysfs"
    device = root / "1-2"
    net = device / "1-2:1.0" / "net" / iface
    net.mkdir(parents=True)
    (device / "serial").write_text(serial + "\n")
    (net / "address").write_text("02:36:0c:7c:14:7a\n")
    (net / "operstate").write_text("up\n")
    return str(root)


def make_uplink(tmp_path, nm: FakeNetworkManager | None = None) -> SmartphoneUplinkService:
    return SmartphoneUplinkService(
        FakeAdb(), nm or FakeNetworkManager(), FakeInterface(), FakeRouting(),
        sysfs_root=make_sysfs(tmp_path),
        iface_wait_attempts=2, iface_wait_delay=0.0,
        dhcp_wait_attempts=2, dhcp_wait_delay=0.0,
    )


def make_dongle(state: DongleState, *, serial: str = SERIAL, interface_name: str | None = None) -> Dongle:
    dongle = Dongle(
        identity=IdentityMaterial(usb_serial="PHONE-1", adb_serial=serial),
        label="téléphone",
        state=state,
        transport=Transport.ADB,
    )
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    dongle.interface_name = interface_name
    return dongle


def seed_real_route(routing: CountingRouting) -> None:
    """Route réelle pré-existante (ne doit jamais être dégradée non forcé)."""
    routing._routes.append(RoutingRoute(10001, "default", "192.168.172.245", IFACE))


async def test_adb_online_applies_bearer_and_resolved_interface(tmp_path, caplog):
    nm = FakeNetworkManager()
    nm.devices[IFACE] = NmIpv4("192.168.172.78", 24)
    nm.gateways[PROFILE] = "192.168.172.245"
    routing = CountingRouting()
    service = UplinkRouteService(FakeModemManager(), routing, smartphone=make_uplink(tmp_path, nm))

    await service.apply_for_dongle(make_dongle(DongleState.ONLINE))

    routes = await routing.list_routes(10001)
    assert [(r.dest, r.via, r.dev, r.type) for r in routes] == [
        ("default", "192.168.172.245", IFACE, "unicast"),
    ]
    assert any(r.source == "192.168.172.78" for r in await routing.list_rules())
    assert routing.barrier_writes == 0


async def test_adb_offline_poses_forced_floor(tmp_path):
    routing = CountingRouting()
    seed_real_route(routing)
    service = UplinkRouteService(FakeModemManager(), routing, smartphone=make_uplink(tmp_path))

    await service.apply_for_dongle(make_dongle(DongleState.OFFLINE))

    assert routing.barrier_writes == 1
    assert [(r.dest, r.type) for r in await routing.list_routes(10001)] == [
        ("default", "unreachable"),
    ]


async def test_adb_unresolvable_interface_non_forced_floor(tmp_path, caplog):
    """Interface introuvable (sysfs sans ce serial) : plancher NON forcé — une
    route réelle pré-existante n'est pas dégradée."""
    routing = CountingRouting()
    seed_real_route(routing)
    service = UplinkRouteService(FakeModemManager(), routing, smartphone=make_uplink(tmp_path))

    with caplog.at_level(logging.WARNING):
        await service.apply_for_dongle(make_dongle(DongleState.ONLINE, serial="UNKNOWN"))

    assert routing.barrier_writes == 0
    assert [(r.dest, r.type) for r in await routing.list_routes(10001)] == [
        ("default", "unicast"),
    ]
    assert any(
        record.levelno == logging.WARNING and "téléphone introuvable" in record.message
        for record in caplog.records
    )


async def test_adb_online_without_lease_forces_floor(tmp_path):
    """Bearer ``None`` (pas de bail DHCP) : ``apply_bearer`` force le plancher."""
    routing = CountingRouting()
    seed_real_route(routing)
    service = UplinkRouteService(FakeModemManager(), routing, smartphone=make_uplink(tmp_path))

    await service.apply_for_dongle(make_dongle(DongleState.ONLINE))

    assert routing.barrier_writes == 1
    assert [(r.dest, r.type) for r in await routing.list_routes(10001)] == [
        ("default", "unreachable"),
    ]
