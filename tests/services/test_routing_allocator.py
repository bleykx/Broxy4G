"""Invariant d'unicité des slots de routage : un proxy local et un dongle
ne doivent jamais recevoir la même table de routage / le même fwmark.
"""

from broxy4g.adapters.local_uplink import LocalInterface
from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import Proxy, ProxySource

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
BEARER = BearerIpConfig(address="10.159.25.48", prefix=27, gateway="10.159.25.49")


def _iface(name="eth0", ip="192.168.1.10", kind="ethernet"):
    return LocalInterface(name=name, ip=ip, kind=kind)


async def test_allocator_gives_distinct_slots_to_proxy_then_dongle(
    dongle_repo, proxy_repo, routing_allocator
):
    """Allocation pour un proxy local (10001/1) puis pour un dongle : slot distinct."""
    proxy = Proxy(
        dongle_id=None,
        source_type=ProxySource.LOCAL,
        source_ref="eth0",
        interface_name="eth0",
        container_name="broxy4g-local",
        listen_port=31250,
    )
    table, fwmark = await routing_allocator.allocate()
    proxy.route_table_id = table
    proxy.fwmark = fwmark
    await proxy_repo.save(proxy)
    assert (table, fwmark) == (10001, 1)

    dongle = Dongle(identity=IdentityMaterial(imei="123456789012345"), label="Test")
    table2, fwmark2 = await routing_allocator.allocate()
    dongle.route_table_id = table2
    dongle.fwmark = fwmark2
    await dongle_repo.save(dongle)

    # L'invariant : table ET fwmark distincts, même fwmark dérivé de la table.
    assert table2 != table
    assert fwmark2 != fwmark
    assert (table2, fwmark2) == (10002, 2)


async def test_proxy_enrolled_before_dongle_connect_gets_distinct_slot(
    local_connections, connection_service, enrollment_service,
    fake_local_uplink, fake_modem,
):
    """Séquence production : le proxy local est provisionné avant le connect du dongle.

    Avant le correctif, le connect du dongle ne voyait QUE les dongles et
    réattribuait 10001/1, écrasant la table du proxy.
    """
    fake_local_uplink.set_interfaces([_iface()])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")
    provisioned = await local_connections.enroll("eth0")
    assert (provisioned.proxy.route_table_id, provisioned.proxy.fwmark) == (10001, 1)

    fake_modem.add_modem(ModemInfo(
        path=MODEM_PATH, imei="123456789012345", usb_serial=None, iccid=None,
        state="Disconnected", interface_name="wwan0",
    ))
    fake_modem.set_bearer_ip(MODEM_PATH, BEARER)

    identity = IdentityMaterial(imei="123456789012345")
    dongle = await enrollment_service.enroll(identity, "Test", "test.apn")
    dongle.interface_name = "wwan0"

    result = await connection_service.connect(dongle.id)

    assert result.state == DongleState.ONLINE
    assert result.route_table_id != provisioned.proxy.route_table_id
    assert result.fwmark != provisioned.proxy.fwmark
    assert (result.route_table_id, result.fwmark) == (10002, 2)
