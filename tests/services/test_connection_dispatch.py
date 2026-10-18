"""Dispatch modem / smartphone dans ConnectionService.

Un dongle ``transport=ADB`` est délégué au ``SmartphoneConnectionService`` ;
un dongle MODEM prend le chemin modem existant ; sans service smartphone,
un dongle ADB échoue avec le message métier exact.
"""

from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.transport import Transport
from broxy4g.services.connection import ConnectionService


class FakeSmartphone:
    """Stub du service smartphone : journalise les délégations et change l'état."""

    def __init__(self):
        self.calls: list[str] = []

    async def connect(self, dongle: Dongle) -> Dongle:
        self.calls.append("connect")
        dongle.change_state(DongleState.CONNECTING)
        dongle.change_state(DongleState.ONLINE)
        return dongle

    async def disconnect(self, dongle: Dongle) -> Dongle:
        self.calls.append("disconnect")
        dongle.change_state(DongleState.OFFLINE)
        return dongle

    async def reset(self, dongle: Dongle) -> Dongle:
        self.calls.append("reset")
        return dongle

    async def bearer_is_live(self, dongle: Dongle) -> bool:
        self.calls.append("bearer_is_live")
        return True


def make_service(
    dongle_repo, fake_modem, fake_iface, fake_routing, fake_nft,
    fake_connectivity, routing_allocator, uplink_routes, isolation,
    smartphone: FakeSmartphone | None = None,
) -> ConnectionService:
    return ConnectionService(
        dongle_repo,
        fake_modem,
        fake_iface,
        fake_routing,
        fake_nft,
        fake_connectivity,
        routing_allocator,
        uplink_routes,
        isolation=isolation,
        modem_wait_attempts=3,
        modem_wait_delay=0.0,
        ip_wait_attempts=3,
        ip_wait_delay=0.0,
        smartphone=smartphone,
    )


async def make_dongle(
    dongle_repo, transport: Transport, usb_serial: str, state: DongleState = DongleState.OFFLINE
) -> Dongle:
    dongle = Dongle(
        identity=IdentityMaterial(usb_serial=usb_serial, adb_serial="ABC123"),
        label="Test",
        state=state,
        transport=transport,
    )
    return await dongle_repo.save(dongle)


async def test_adb_dongle_is_delegated(
    dongle_repo, fake_modem, fake_iface, fake_routing, fake_nft,
    fake_connectivity, routing_allocator, uplink_routes, isolation,
):
    fake = FakeSmartphone()
    service = make_service(
        dongle_repo, fake_modem, fake_iface, fake_routing, fake_nft,
        fake_connectivity, routing_allocator, uplink_routes, isolation, smartphone=fake,
    )
    dongle = await make_dongle(dongle_repo, Transport.ADB, "PHONE-1")

    connected = await service.connect(dongle.id)

    assert fake.calls == ["connect"]
    assert connected.state == DongleState.ONLINE

    await service.disconnect(dongle.id)
    live = await service.bearer_is_live(await dongle_repo.get_by_id(dongle.id))
    await service.reset_modem(dongle.id)

    assert fake.calls == ["connect", "disconnect", "bearer_is_live", "reset"]
    assert live is True


async def test_modem_dongle_still_takes_modem_path(
    dongle_repo, fake_modem, fake_iface, fake_routing, fake_nft,
    fake_connectivity, routing_allocator, uplink_routes, isolation,
):
    fake = FakeSmartphone()
    service = make_service(
        dongle_repo, fake_modem, fake_iface, fake_routing, fake_nft,
        fake_connectivity, routing_allocator, uplink_routes, isolation, smartphone=fake,
    )
    fake_modem.add_modem(ModemInfo(
        path="wwan0", imei=None, iccid=None, usb_serial="MO-1",
        state="connected", interface_name="wwan0",
    ))
    fake_modem.set_bearer_ip(
        "wwan0", BearerIpConfig(address="10.159.56.48", prefix=27, gateway="10.159.56.49")
    )
    dongle = await make_dongle(dongle_repo, Transport.MODEM, "MO-1")

    connected = await service.connect(dongle.id)

    assert fake.calls == []  # aucune délégation au service smartphone
    assert connected.state == DongleState.ONLINE


async def test_adb_dongle_without_smartphone_service_fails(
    dongle_repo, fake_modem, fake_iface, fake_routing, fake_nft,
    fake_connectivity, routing_allocator, uplink_routes, isolation,
):
    service = make_service(
        dongle_repo, fake_modem, fake_iface, fake_routing, fake_nft,
        fake_connectivity, routing_allocator, uplink_routes, isolation,
    )  # smartphone=None
    # CONFIGURING : état où une transition vers ERROR est autorisée par la
    # machine à états (OFFLINE n'y autorise pas).
    dongle = await make_dongle(dongle_repo, Transport.ADB, "PHONE-1", DongleState.CONFIGURING)

    result = await service.connect(dongle.id)

    assert result.state == DongleState.ERROR
    assert result.last_error == "transport ADB non configuré"
