"""Tests du service de (re)pose idempotente des routes d'uplink d'un dongle."""

import logging

from broxy4g.adapters.modem_manager import BearerIpConfig, FakeModemManager, ModemInfo
from broxy4g.adapters.routing import FakeRouting
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.services.uplink_routes import UplinkRouteService

BEARER = BearerIpConfig(address="10.159.56.48", prefix=27, gateway="10.159.56.49")
NO_GATEWAY = BearerIpConfig(address="10.159.56.48", prefix=27)


class CountingRouting(FakeRouting):
    """FakeRouting qui compte les écritures (pour prouver l'absence d'écritures inutiles)."""

    def __init__(self):
        super().__init__()
        self.writes = 0

    async def add_rule(self, table_id, fwmark):
        self.writes += 1
        await super().add_rule(table_id, fwmark)

    async def add_route(self, table_id, dest, via, dev):
        self.writes += 1
        await super().add_route(table_id, dest, via, dev)

    async def add_source_rule(self, address, table_id):
        self.writes += 1
        await super().add_source_rule(address, table_id)


def make_dongle() -> Dongle:
    # État attribué directement : le service ne fait que lire l'état du dongle,
    # il ne le modifie jamais (et ne doit pas dépendre de la machine à états).
    dongle = Dongle(
        identity=IdentityMaterial(imei="123456789012345"),
        label="Test",
        apn="test.apn",
        state=DongleState.ONLINE,
    )
    dongle.interface_name = "dongle-1336"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    return dongle


def make_modem(bearer: BearerIpConfig | None) -> FakeModemManager:
    fake_modem = FakeModemManager()
    fake_modem.add_modem(ModemInfo(
        path="wwan0", imei="123456789012345", iccid=None,
        usb_serial=None, state="connected", interface_name="dongle-1336",
    ))
    if bearer is not None:
        fake_modem.set_bearer_ip("wwan0", bearer)
    return fake_modem


async def test_apply_repairs_wrong_default_route():
    """Table contenant la route FAUSSE du LAN : remplacée par celle du bearer."""
    routing = CountingRouting()
    await routing.add_rule(10001, 1)
    await routing.add_route(10001, "default", "192.168.1.254", "eth0")  # route écrasée

    service = UplinkRouteService(make_modem(BEARER), routing)
    await service.apply_for_dongle(make_dongle())

    routes = await routing.list_routes(10001)
    assert [(r.dest, r.via, r.dev) for r in routes] == [
        ("default", "10.159.56.49", "dongle-1336"),
    ]
    assert any(
        r.table_id == 10001 and r.source == "10.159.56.48"
        for r in await routing.list_rules()
    )


async def test_apply_without_gateway_writes_link_route_and_warns(caplog):
    """Bearer sans gateway : 'default dev <iface>' (scope link) + WARNING."""
    routing = CountingRouting()
    service = UplinkRouteService(make_modem(NO_GATEWAY), routing)

    with caplog.at_level(logging.WARNING):
        await service.apply_for_dongle(make_dongle())

    routes = await routing.list_routes(10001)
    assert [(r.dest, r.via, r.dev) for r in routes] == [("default", None, "dongle-1336")]
    assert any(
        record.levelno == logging.WARNING and "sans passerelle" in record.message
        for record in caplog.records
    )


async def test_apply_is_idempotent_without_writes():
    """Deux appels consécutifs sur un état correct : aucune écriture au second."""
    routing = CountingRouting()
    service = UplinkRouteService(make_modem(BEARER), routing)
    dongle = make_dongle()

    await service.apply_for_dongle(dongle)
    first_pass_writes = routing.writes
    assert first_pass_writes > 0

    await service.apply_for_dongle(dongle)

    assert routing.writes == first_pass_writes


async def test_apply_without_bearer_only_ensures_fwmark_rule():
    """Sans bearer courant : la règle fwmark est posée, aucune route."""
    routing = CountingRouting()
    service = UplinkRouteService(make_modem(None), routing)

    await service.apply_for_dongle(make_dongle())

    assert any(
        r.table_id == 10001 and r.fwmark == 1 for r in await routing.list_rules()
    )
    assert await routing.list_routes(10001) == []


async def test_apply_noop_without_routing_slot():
    """Dongle sans table/fwmark alloués : aucune écriture."""
    routing = CountingRouting()
    service = UplinkRouteService(make_modem(BEARER), routing)
    dongle = make_dongle()
    dongle.route_table_id = None
    dongle.fwmark = None

    await service.apply_for_dongle(dongle)

    assert routing.writes == 0
