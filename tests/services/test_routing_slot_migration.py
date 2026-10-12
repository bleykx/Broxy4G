import logging
from unittest.mock import AsyncMock, patch

from broxy4g.adapters.local_uplink import LocalInterface
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import Proxy, ProxySource

LOGGER = "broxy4g.services.routing_slot_migration"


def _dongle(*, table=10001, fwmark=1, imei="123456789012345"):
    dongle = Dongle(identity=IdentityMaterial(imei=imei), label="Test")
    dongle.interface_name = "wwan0"
    dongle.route_table_id = table
    dongle.fwmark = fwmark
    return dongle


def _local_proxy(*, interface="eth0", table=10001, fwmark=1, port=31250):
    proxy = Proxy(
        dongle_id=None,
        source_type=ProxySource.LOCAL,
        source_ref=interface,
        interface_name=interface,
        container_name=f"broxy4g-local-{port}",
        listen_port=port,
    )
    proxy.network_ip = "172.28.0.150"
    proxy.route_table_id = table
    proxy.fwmark = fwmark
    return proxy


async def test_conflict_with_dongle_releases_local_proxy(
    slot_migration, dongle_repo, proxy_repo, caplog
):
    """Dongle(10001,1) + proxy local(10001,1) : le dongle garde, le proxy est libéré."""
    await dongle_repo.save(_dongle())
    proxy = await proxy_repo.save(_local_proxy())

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await slot_migration.repair_duplicates()

    saved_dongle = (await dongle_repo.list_all())[0]
    assert saved_dongle.route_table_id == 10001
    assert saved_dongle.fwmark == 1
    reloaded = await proxy_repo.get_by_id(proxy.id)
    assert reloaded.route_table_id is None
    assert reloaded.fwmark is None
    assert any(
        record.levelno == logging.INFO and str(proxy.id) in record.getMessage()
        for record in caplog.records
    )


async def test_second_call_is_noop(slot_migration, dongle_repo, proxy_repo, caplog):
    """Un second appel ne sauvegarde ni ne journalise rien (idempotence)."""
    await dongle_repo.save(_dongle())
    proxy = await proxy_repo.save(_local_proxy())

    with (
        caplog.at_level(logging.INFO, logger=LOGGER),
        patch.object(slot_migration.proxy_repo, "save", new=AsyncMock(wraps=slot_migration.proxy_repo.save)) as mock_save,
    ):
        await slot_migration.repair_duplicates()
        mock_save.assert_awaited_once()
        before = len(caplog.records)
        await slot_migration.repair_duplicates()

    mock_save.assert_awaited_once()
    assert len(caplog.records) == before
    assert (await proxy_repo.get_by_id(proxy.id)).route_table_id is None


async def test_no_conflict_no_write(slot_migration, dongle_repo, proxy_repo):
    """Sans conflit, aucune écriture en base et aucun état modifié."""
    await dongle_repo.save(_dongle(table=10001, fwmark=1))
    proxy = await proxy_repo.save(_local_proxy(table=10002, fwmark=2, port=31251))

    with patch.object(proxy_repo, "save", new=AsyncMock(wraps=proxy_repo.save)) as mock_save:
        await slot_migration.repair_duplicates()

    mock_save.assert_not_awaited()
    reloaded = await proxy_repo.get_by_id(proxy.id)
    assert reloaded.route_table_id == 10002
    assert reloaded.fwmark == 2


async def test_two_local_proxies_same_slot_keeps_lowest_port(slot_migration, proxy_repo):
    """Deux proxies locaux sur le même slot : le plus petit port est conservé."""
    low = await proxy_repo.save(_local_proxy(interface="eth0", port=31250))
    high = await proxy_repo.save(_local_proxy(interface="wlan0", port=31251))

    await slot_migration.repair_duplicates()

    kept = await proxy_repo.get_by_id(low.id)
    assert kept.route_table_id == 10001
    assert kept.fwmark == 1
    released = await proxy_repo.get_by_id(high.id)
    assert released.route_table_id is None
    assert released.fwmark is None


async def test_two_dongles_same_slot_untouched(slot_migration, dongle_repo, caplog):
    """Deux dongles sur le même slot : WARNING et aucun changement."""
    await dongle_repo.save(_dongle(imei="111111111111111", table=10001, fwmark=1))
    await dongle_repo.save(_dongle(imei="222222222222222", table=10001, fwmark=1))

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await slot_migration.repair_duplicates()

    dongles = await dongle_repo.list_all()
    assert [d.route_table_id for d in dongles] == [10001, 10001]
    assert all(d.fwmark == 1 for d in dongles)
    assert any(
        record.levelno == logging.WARNING and record.name == LOGGER
        for record in caplog.records
    )


async def test_repair_then_restore_pins_proxy_on_own_slot(
    slot_migration, dongle_repo, proxy_repo, local_connections, fake_local_uplink, fake_routing
):
    """Après repair, restore() ré-épingle le proxy sur un slot distinct du dongle."""
    dongle = await dongle_repo.save(_dongle(table=10001, fwmark=1))
    proxy = await proxy_repo.save(_local_proxy(interface="eth0", table=10001, fwmark=1))
    fake_local_uplink.set_interfaces([LocalInterface("eth0", "192.168.1.10", "ethernet")])
    fake_local_uplink.set_gateway("eth0", "192.168.1.1")

    await slot_migration.repair_duplicates()
    await local_connections.restore("eth0")

    reloaded = await proxy_repo.get_by_id(proxy.id)
    assert reloaded.route_table_id is not None
    assert reloaded.route_table_id != dongle.route_table_id
    assert reloaded.fwmark != dongle.fwmark
    rules = await fake_routing.list_rules()
    assert any(
        r.table_id == reloaded.route_table_id and r.fwmark == reloaded.fwmark for r in rules
    )
    routes = await fake_routing.list_routes(reloaded.route_table_id)
    assert [(r.dest, r.via, r.dev) for r in routes] == [("default", "192.168.1.1", "eth0")]
