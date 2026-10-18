"""Tests du service de (re)pose idempotente des routes d'uplink d'un dongle.

Couvre aussi le plancher : une table dédiée ne doit jamais rester sans route
``default`` (sinon le noyau retombe sur la table ``main`` et le proxy fuit).
"""

import logging

from broxy4g.adapters.modem_manager import BearerIpConfig, FakeModemManager, ModemInfo
from broxy4g.adapters.routing import FakeRouting, RoutingRoute
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
        self.barrier_writes = 0

    async def add_rule(self, table_id, fwmark):
        self.writes += 1
        await super().add_rule(table_id, fwmark)

    async def add_route(self, table_id, dest, via, dev):
        self.writes += 1
        await super().add_route(table_id, dest, via, dev)

    async def add_barrier_route(self, table_id):
        self.writes += 1
        self.barrier_writes += 1
        await super().add_barrier_route(table_id)

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


async def test_apply_replaces_foreign_default_route_from_nm(caplog):
    """Une /0 ÉTRANGÈRE (NetworkManager : ``proto dhcp`` + ``src`` autre) dans
    la table dédiée est TOUJOURS remplacée par notre route, avec un
    avertissement explicite (frontière d'isolation)."""
    routing = CountingRouting()
    await routing.add_rule(10001, 1)
    routing._routes.append(RoutingRoute(
        table_id=10001, dest="default", via="192.168.172.245",
        dev="enx02360c7c147a", proto="dhcp", src="192.168.172.79",
    ))
    service = UplinkRouteService(make_modem(BEARER), routing)

    with caplog.at_level(logging.WARNING):
        await service.apply_for_dongle(make_dongle())

    routes = await routing.list_routes(10001)
    assert [(r.dest, r.via, r.dev, r.proto, r.src) for r in routes] == [
        ("default", "10.159.56.49", "dongle-1336", None, None),
    ]
    assert any(
        record.levelno == logging.WARNING and "ÉTRANGÈRE" in record.message
        for record in caplog.records
    )


async def test_apply_replaces_default_route_with_foreign_src():
    """/0 avec dev+via identiques aux nôtres mais ``src`` != adresse du
    bearer : la route est étrangère, elle est remplacée (et le second appel
    ne produit plus aucune écriture)."""
    routing = CountingRouting()
    await routing.add_rule(10001, 1)
    routing._routes.append(RoutingRoute(
        table_id=10001, dest="default", via="10.159.56.49",
        dev="dongle-1336", src="192.168.172.79",
    ))
    service = UplinkRouteService(make_modem(BEARER), routing)
    dongle = make_dongle()

    await service.apply_for_dongle(dongle)

    routes = await routing.list_routes(10001)
    assert [(r.via, r.dev, r.proto, r.src) for r in routes] == [
        ("10.159.56.49", "dongle-1336", None, None),
    ]
    writes_after_repair = routing.writes

    await service.apply_for_dongle(dongle)

    assert routing.writes == writes_after_repair


async def test_apply_keeps_our_default_route_without_writes():
    """/0 présente avec dev+via corrects, ``proto`` absent et ``src`` égal à
    l'adresse du bearer : c'est la nôtre, aucune écriture du tout."""
    routing = CountingRouting()
    await routing.add_rule(10001, 1)
    routing._routes.append(RoutingRoute(
        table_id=10001, dest="default", via="10.159.56.49",
        dev="dongle-1336", src="10.159.56.48",
    ))
    await routing.add_source_rule("10.159.56.48", 10001)
    service = UplinkRouteService(make_modem(BEARER), routing)

    writes_before = routing.writes
    await service.apply_for_dongle(make_dongle())

    assert routing.writes == writes_before
    routes = await routing.list_routes(10001)
    assert [(r.dest, r.via, r.dev, r.proto, r.src) for r in routes] == [
        ("default", "10.159.56.49", "dongle-1336", None, "10.159.56.48"),
    ]


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


async def test_apply_without_bearer_ensures_fwmark_rule_and_barrier():
    """Sans bearer courant : règle fwmark posée et plancher, aucune route réelle."""
    routing = CountingRouting()
    service = UplinkRouteService(make_modem(None), routing)

    await service.apply_for_dongle(make_dongle())

    assert any(
        r.table_id == 10001 and r.fwmark == 1 for r in await routing.list_rules()
    )
    routes = await routing.list_routes(10001)
    assert [(r.dest, r.type) for r in routes] == [("default", "unreachable")]


async def test_apply_bearer_without_interface_poses_barrier():
    """Slot sans interface connue : plancher posé, aucune route réelle."""
    routing = CountingRouting()
    service = UplinkRouteService(make_modem(BEARER), routing)

    await service.apply_bearer(make_dongle(), BEARER, None)

    routes = await routing.list_routes(10001)
    assert [(r.dest, r.type) for r in routes] == [("default", "unreachable")]
    assert all(r.type != "unicast" for r in routes)
    # Pas de règle source : il n'y a pas de source à épingler.
    assert not any(
        r.table_id == 10001 and r.source is not None for r in await routing.list_rules()
    )


async def test_apply_bearer_without_ipv4_poses_barrier():
    """Slot + bearer sans IPv4 (address vide) : plancher posé, aucune route réelle."""
    routing = CountingRouting()
    service = UplinkRouteService(make_modem(BEARER), routing)
    no_ip = BearerIpConfig(address="", prefix=27, gateway="10.159.56.49")

    await service.apply_bearer(make_dongle(), no_ip, "dongle-1336")

    routes = await routing.list_routes(10001)
    assert [(r.dest, r.type) for r in routes] == [("default", "unreachable")]


async def test_apply_barrier_is_idempotent():
    """Deux appels de apply_barrier : aucune écriture au second."""
    routing = CountingRouting()
    service = UplinkRouteService(make_modem(BEARER), routing)
    dongle = make_dongle()

    await service.apply_barrier(dongle)
    first_pass_writes = routing.writes
    assert first_pass_writes > 0

    await service.apply_barrier(dongle)

    assert routing.writes == first_pass_writes


async def test_apply_replaces_barrier_with_real_route_then_idempotent():
    """Dongle ONLINE + bearer avec passerelle : la route réelle remplace le
    plancher ; le second appel ne produit aucune écriture supplémentaire."""
    routing = CountingRouting()
    await routing.add_barrier_route(10001)  # plancher résiduel
    service = UplinkRouteService(make_modem(BEARER), routing)
    dongle = make_dongle()

    await service.apply_for_dongle(dongle)

    routes = await routing.list_routes(10001)
    assert [(r.dest, r.via, r.dev, r.type) for r in routes] == [
        ("default", "10.159.56.49", "dongle-1336", "unicast"),
    ]
    first_pass_writes = routing.writes

    await service.apply_for_dongle(dongle)

    assert routing.writes == first_pass_writes
    routes = await routing.list_routes(10001)
    assert [(r.dest, r.type) for r in routes] == [("default", "unicast")]


async def test_apply_for_dongle_inactive_state_poses_barrier():
    """Dongle non actif (ERROR/OFFLINE) avec modem résolu : plancher,
    pas de route réelle."""
    for state in (DongleState.ERROR, DongleState.OFFLINE):
        routing = CountingRouting()
        service = UplinkRouteService(make_modem(BEARER), routing)
        dongle = make_dongle()
        dongle.state = state

        await service.apply_for_dongle(dongle)

        routes = await routing.list_routes(10001)
        assert [(r.dest, r.type) for r in routes] == [("default", "unreachable")]
        assert any(
            r.table_id == 10001 and r.fwmark == 1 for r in await routing.list_rules()
        )


async def test_barrier_non_forced_never_degrades_real_route():
    """Une route RÉELLE déjà présente : apply_barrier (non forcé) ne pose
    PAS le plancher (aucune écriture)."""
    routing = CountingRouting()
    await routing.add_rule(10001, 1)
    await routing.add_route(10001, "default", "10.159.56.49", "dongle-1336")
    service = UplinkRouteService(make_modem(BEARER), routing)
    writes_before = routing.writes
    barrier_writes_before = routing.barrier_writes

    await service.apply_barrier(make_dongle())

    assert routing.writes == writes_before
    assert routing.barrier_writes == barrier_writes_before
    assert [(r.dest, r.type) for r in await routing.list_routes(10001)] == [
        ("default", "unicast"),
    ]


async def test_barrier_forced_replaces_real_route():
    """apply_barrier(force=True) avec une route réelle présente : le plancher
    remplace la route réelle."""
    routing = CountingRouting()
    await routing.add_rule(10001, 1)
    await routing.add_route(10001, "default", "10.159.56.49", "dongle-1336")
    service = UplinkRouteService(make_modem(BEARER), routing)

    await service.apply_barrier(make_dongle(), force=True)

    assert routing.barrier_writes == 1
    assert [(r.dest, r.type) for r in await routing.list_routes(10001)] == [
        ("default", "unreachable"),
    ]


async def test_apply_bearer_missing_bearer_forces_barrier_over_stale_route():
    """Bearer absent alors qu'une route réelle existe : plancher FORCÉ posé
    (l'ancienne route réelle n'a plus de source valide)."""
    routing = CountingRouting()
    await routing.add_rule(10001, 1)
    await routing.add_route(10001, "default", "10.159.56.49", "dongle-1336")
    service = UplinkRouteService(make_modem(None), routing)

    await service.apply_bearer(make_dongle(), None, "dongle-1336")

    assert routing.barrier_writes == 1
    assert [(r.dest, r.type) for r in await routing.list_routes(10001)] == [
        ("default", "unreachable"),
    ]


async def test_apply_for_dongle_active_without_interface_non_forced_barrier():
    """Actif sans interface connue : plancher NON forcé — une route réelle
    pré-existante n'est pas touchée, table vide ⇒ plancher posé."""
    service_with_route = UplinkRouteService(make_modem(BEARER), (routing := CountingRouting()))
    await routing.add_rule(10001, 1)
    await routing.add_route(10001, "default", "10.159.56.49", "dongle-1336")
    dongle = make_dongle()
    dongle.interface_name = None

    await service_with_route.apply_for_dongle(dongle)

    assert routing.barrier_writes == 0
    assert [(r.dest, r.type) for r in await routing.list_routes(10001)] == [
        ("default", "unicast"),
    ]

    empty_routing = CountingRouting()
    dongle_empty = make_dongle()
    dongle_empty.interface_name = None
    await UplinkRouteService(make_modem(BEARER), empty_routing).apply_for_dongle(dongle_empty)

    assert empty_routing.barrier_writes == 1
    assert [(r.dest, r.type) for r in await empty_routing.list_routes(10001)] == [
        ("default", "unreachable"),
    ]


async def test_apply_for_dongle_active_modem_not_found_non_forced_barrier():
    """Actif avec modem introuvable : plancher NON forcé — une route réelle
    pré-existante n'est pas touchée."""
    routing = CountingRouting()
    await routing.add_rule(10001, 1)
    await routing.add_route(10001, "default", "10.159.56.49", "dongle-1336")
    service = UplinkRouteService(FakeModemManager(), routing)  # aucun modem

    await service.apply_for_dongle(make_dongle())

    assert routing.barrier_writes == 0
    assert [(r.dest, r.type) for r in await routing.list_routes(10001)] == [
        ("default", "unicast"),
    ]


async def test_apply_noop_without_routing_slot():
    """Dongle sans table/fwmark alloués : aucune écriture."""
    routing = CountingRouting()
    service = UplinkRouteService(make_modem(BEARER), routing)
    dongle = make_dongle()
    dongle.route_table_id = None
    dongle.fwmark = None

    await service.apply_for_dongle(dongle)

    assert routing.writes == 0
