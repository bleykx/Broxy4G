"""SmartphoneConnectionService : fakes ADB / NetworkManager / Interface / Routing + sysfs tmp."""

from broxy4g.adapters.adb import AdbDevice, FakeAdb
from broxy4g.adapters.modem_manager import FakeModemManager
from broxy4g.adapters.net_interface import FakeInterface
from broxy4g.adapters.network_manager import FakeNetworkManager, NmIpv4
from broxy4g.adapters.routing import FakeRouting
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.transport import Transport
from broxy4g.services.smartphone_connection import SmartphoneConnectionService
from broxy4g.services.smartphone_uplink import SmartphoneUplinkService
from broxy4g.services.uplink_routes import UplinkRouteService

SERIAL = "ABC123"
IFACE = "enx02360c7c147a"
PROFILE = f"broxy4g-phone-{SERIAL}"
BEARER_IP = "192.168.172.78"


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


class FakeIsolation:
    def __init__(self):
        self.applied = 0

    async def apply(self):
        self.applied += 1


def make_dongle(state: DongleState) -> Dongle:
    return Dongle(
        identity=IdentityMaterial(usb_serial="PHONE-1", adb_serial=SERIAL),
        label="téléphone",
        state=state,
        transport=Transport.ADB,
    )


def make_service(
    tmp_path,
    adb: FakeAdb | None = None,
    nm: FakeNetworkManager | None = None,
    iface: FakeInterface | None = None,
    routing: FakeRouting | None = None,
    repo: FakeRepo | None = None,
    allocator: FakeAllocator | None = None,
    isolation: FakeIsolation | None = None,
):
    """SmartphoneConnectionService câblé avec le vrai SmartphoneUplinkService en fakes."""
    adb = adb or FakeAdb()
    nm = nm or FakeNetworkManager()
    uplink = SmartphoneUplinkService(
        adb, nm, iface or FakeInterface(), FakeRouting(),
        sysfs_root=make_sysfs(tmp_path),
        iface_wait_attempts=2, iface_wait_delay=0.0,
        dhcp_wait_attempts=2, dhcp_wait_delay=0.0,
    )
    return SmartphoneConnectionService(
        repo or FakeRepo(),
        uplink,
        iface or FakeInterface(),
        routing or FakeRouting(),
        allocator or FakeAllocator(),
        UplinkRouteService(FakeModemManager(), routing or FakeRouting()),
        isolation=isolation,
        ip_wait_attempts=3,
        ip_wait_delay=0.0,
    )


def make_nm_with_lease() -> FakeNetworkManager:
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4(BEARER_IP, 24)
    nm.gateways[PROFILE] = "192.168.172.245"
    return nm


async def test_connect_nominal_online_routes_and_isolation(tmp_path):
    routing, repo, isolation = FakeRouting(), FakeRepo(), FakeIsolation()
    service = make_service(tmp_path, nm=make_nm_with_lease(), routing=routing, repo=repo, isolation=isolation)

    dongle = await service.connect(make_dongle(DongleState.OFFLINE))

    assert dongle.state == DongleState.ONLINE
    assert dongle.interface_name == IFACE
    assert dongle.route_table_id == 10001 and dongle.fwmark == 7
    routes = await routing.list_routes(10001)
    assert [(r.dest, r.via, r.dev, r.type) for r in routes] == [
        ("default", "192.168.172.245", IFACE, "unicast"),
    ]
    assert any(r.source == BEARER_IP for r in await routing.list_rules())
    assert isolation.applied == 1
    assert len(repo.saved) == 2  # slot + ONLINE


async def test_connect_bring_up_failure_marks_error_and_floor(tmp_path):
    adb = FakeAdb()
    adb.fail_enable_times = 3  # tous les essais d'activation du partage échouent
    routing, isolation = FakeRouting(), FakeIsolation()
    service = make_service(tmp_path, adb=adb, nm=make_nm_with_lease(), routing=routing, isolation=isolation)

    dongle = await service.connect(make_dongle(DongleState.OFFLINE))

    assert dongle.state == DongleState.ERROR
    assert dongle.last_error.startswith("échec connexion smartphone: ")
    # Plancher anti-fuite présent (allocé + rescellé sur l'échec).
    routes = await routing.list_routes(10001)
    assert [(r.dest, r.type) for r in routes] == [("default", "unreachable")]
    assert isolation.applied == 1


async def test_disconnect_offline_and_bring_down(tmp_path):
    adb, nm, routing = FakeAdb(), make_nm_with_lease(), FakeRouting()
    await nm.ensure_profile(IFACE, PROFILE)
    service = make_service(tmp_path, adb=adb, nm=nm, routing=routing)
    dongle = make_dongle(DongleState.ONLINE)
    dongle.route_table_id, dongle.fwmark, dongle.interface_name = 10001, 7, IFACE

    result = await service.disconnect(dongle)

    assert result.state == DongleState.OFFLINE
    assert ["down", PROFILE] in nm.calls
    assert ["svc", "usb", "setFunctions", "mtp"] in adb.calls
    routes = await routing.list_routes(10001)
    assert [(r.dest, r.type) for r in routes] == [("default", "unreachable")]


async def test_reset_airplane_cycle_then_online(tmp_path):
    adb = FakeAdb()
    adb.devices = [AdbDevice(serial=SERIAL, state="device")]
    nm, routing = make_nm_with_lease(), FakeRouting()
    await nm.ensure_profile(IFACE, PROFILE)
    service = make_service(tmp_path, adb=adb, nm=nm, routing=routing)
    dongle = make_dongle(DongleState.RESETTING)
    dongle.route_table_id, dongle.fwmark, dongle.interface_name = 10001, 7, IFACE

    result = await service.reset(dongle)

    assert result.state == DongleState.ONLINE
    # Le cycle radio (mode avion) a bien été effectué.
    assert ["cmd", "connectivity", "airplane-mode", "enable"] in adb.calls
    assert ["cmd", "connectivity", "airplane-mode", "disable"] in adb.calls
    # Le bail a été libéré avant la coupure radio.
    assert ["down", PROFILE] in nm.calls
    # La reconnexion a reposé la route réelle.
    assert [(r.dest, r.type) for r in await routing.list_routes(10001)] == [("default", "unicast")]


async def test_bearer_is_live_false_without_ip(tmp_path):
    iface = FakeInterface()  # aucune adresse sur IFACE
    service = make_service(tmp_path, nm=make_nm_with_lease(), iface=iface)
    dongle = make_dongle(DongleState.ONLINE)
    dongle.route_table_id, dongle.fwmark, dongle.interface_name = 10001, 7, IFACE

    assert await service.bearer_is_live(dongle) is False
