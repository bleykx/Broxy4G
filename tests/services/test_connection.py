from unittest.mock import AsyncMock, patch

import pytest

from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.services.connection import ConnectionService

MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
BEARER = BearerIpConfig(address="10.159.25.48", prefix=27, gateway="10.159.25.49")


def add_modem(fake_modem, path=MODEM_PATH, imei="123456789012345", interface="wwan0", bearer: BearerIpConfig | None = BEARER):
    fake_modem.add_modem(ModemInfo(
        path=path, imei=imei, usb_serial=None, iccid=None,
        state="Disconnected", interface_name=interface,
    ))
    if bearer is not None:
        fake_modem.set_bearer_ip(path, bearer)


@pytest.mark.asyncio
async def test_connect_transitions_to_online(connection_service, enrollment_service, fake_modem):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    add_modem(fake_modem)

    result = await connection_service.connect(dongle.id)
    assert result.state == DongleState.ONLINE
    # Contrat : aucun renommage d'interface (le nom persisté est celui du modem).
    assert result.interface_name == "wwan0"


@pytest.mark.asyncio
async def test_disconnect_transitions_to_offline(connection_service, enrollment_service, fake_modem):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    add_modem(fake_modem)
    await connection_service.connect(dongle.id)

    result = await connection_service.disconnect(dongle.id)
    assert result.state == DongleState.OFFLINE


@pytest.mark.asyncio
async def test_disconnect_removes_routing_rules(connection_service, enrollment_service, fake_routing):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    await enrollment_service.dongle_repo.save(dongle)

    await connection_service.routing.add_rule(10001, 1)
    await connection_service.routing.add_route(10001, "default", None, "wwan0")

    await connection_service.disconnect(dongle.id)

    assert await fake_routing.list_rules() == []
    assert await fake_routing.list_routes(10001) == []


@pytest.mark.asyncio
async def test_get_metrics_returns_metrics(
    connection_service, enrollment_service, fake_iface, fake_connectivity
):
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    fake_iface.set_address("wwan0", "10.159.25.48")
    await enrollment_service.dongle_repo.save(dongle)

    fake_connectivity.set_public_ip("1.2.3.4")
    metrics = await connection_service.get_metrics(dongle.id)
    assert metrics.public_ip == "1.2.3.4"
    assert metrics.ping is not None


@pytest.mark.asyncio
async def test_get_metrics_pins_active_source(
    connection_service, enrollment_service, fake_iface, fake_connectivity
):
    """Les probes sont épinglés à l'IP source du bearer, jamais au nom d'interface."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    fake_iface.set_address("wwan0", "10.159.25.48")
    await enrollment_service.dongle_repo.save(dongle)

    fake_connectivity.set_public_ip("1.2.3.4")
    await connection_service.get_metrics(dongle.id)

    assert fake_connectivity.interface_calls == ["10.159.25.48", "10.159.25.48"]


@pytest.mark.asyncio
async def test_get_metrics_without_source_skips_probes(
    connection_service, enrollment_service, fake_iface, fake_connectivity
):
    """Sans IP source, aucun probe n'est lancé (pas de fuite par la table main)."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"

    metrics = await connection_service.get_metrics(dongle.id)
    assert metrics.public_ip is None
    assert metrics.ping is None
    assert fake_connectivity.interface_calls == []


@pytest.mark.asyncio
async def test_connect_handles_port_change(connection_service, enrollment_service, fake_modem):
    """connect resolves modem by identity when USB port changes."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    await enrollment_service.dongle_repo.save(dongle)

    add_modem(
        fake_modem,
        path="/org/freedesktop/ModemManager1/Modem/1",
        interface="wwan1",
        bearer=BearerIpConfig(address="10.0.0.2", prefix=30, gateway="10.0.0.1"),
    )

    with patch.object(fake_modem, "connect", new=AsyncMock()) as mock_connect:
        result = await connection_service.connect(dongle.id)

    assert result.state == DongleState.ONLINE
    # L'application ne renomme jamais : l'interface est celle du modem.
    assert result.interface_name == "wwan1"
    mock_connect.assert_awaited_once_with("/org/freedesktop/ModemManager1/Modem/1", "test.apn", None)


@pytest.mark.asyncio
async def test_connect_handles_absent_modem(connection_service, enrollment_service, fake_modem):
    """connect transitions to ERROR when modem is absent."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"

    with patch.object(fake_modem, "connect", new=AsyncMock()) as mock_connect:
        result = await connection_service.connect(dongle.id)

    assert result.state == DongleState.ERROR
    mock_connect.assert_not_awaited()
    # Message métier distinct du cas « modem sans interface réseau ».
    assert result.last_error == "modem introuvable (IMEI=123456789012345)"
    assert "port net" not in (result.last_error or "")


@pytest.mark.asyncio
async def test_disconnect_handles_resolved_path(connection_service, enrollment_service, fake_modem):
    """disconnect resolves modem by identity when USB port changes."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan1"

    add_modem(
        fake_modem,
        path="/org/freedesktop/ModemManager1/Modem/1",
        interface="wwan1",
        bearer=None,
    )

    with patch.object(fake_modem, "disconnect", new=AsyncMock()) as mock_disconnect:
        result = await connection_service.disconnect(dongle.id)

    assert result.state == DongleState.OFFLINE
    mock_disconnect.assert_awaited_once_with("/org/freedesktop/ModemManager1/Modem/1")


@pytest.mark.asyncio
async def test_connect_modem_raises_error(connection_service, enrollment_service, fake_modem):
    """connect transitions to ERROR when modem.connect raises."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    add_modem(fake_modem)

    with patch.object(fake_modem, "connect", new=AsyncMock(side_effect=RuntimeError("not authorized"))):
        result = await connection_service.connect(dongle.id)

    assert result.state == DongleState.ERROR
    saved = await connection_service.dongle_repo.get_by_id(dongle.id)
    assert saved.state == DongleState.ERROR


@pytest.mark.asyncio
async def test_connect_ip_wait_timeout_error(connection_service, enrollment_service, fake_modem):
    """connect transitions to ERROR when no IP is assigned."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    # Modem sans bearer : rien n'est assigné, l'interface reste sans IPv4.
    add_modem(fake_modem, bearer=None)

    service = ConnectionService(
        connection_service.dongle_repo, fake_modem, connection_service.iface,
        connection_service.routing, connection_service.nft,
        connection_service.connectivity,
        connection_service.allocator,
        connection_service.uplink_routes,
        ip_wait_attempts=1, ip_wait_delay=0.01,
    )

    result = await service.connect(dongle.id)
    assert result.state == DongleState.ERROR


@pytest.mark.asyncio
async def test_connect_persists_error_detail_on_modem_failure(
    connection_service, enrollment_service, fake_modem
):
    """Lorsque modem.connect lève, le dongle persisté est en ERROR et last_error détaille l'erreur."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    add_modem(fake_modem)

    with patch.object(fake_modem, "connect", new=AsyncMock(side_effect=RuntimeError("not authorized"))):
        result = await connection_service.connect(dongle.id)

    assert result.state == DongleState.ERROR
    saved = await enrollment_service.dongle_repo.get_by_id(dongle.id)
    assert saved.state == DongleState.ERROR
    assert saved.last_error is not None
    assert "not authorized" in saved.last_error
    assert "RuntimeError" in saved.last_error


@pytest.mark.asyncio
async def test_connect_clears_error_on_success(connection_service, enrollment_service, fake_modem):
    """Après un connect réussi, last_error est remis à None."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    dongle.last_error = "ancienne erreur"
    await enrollment_service.dongle_repo.save(dongle)

    add_modem(fake_modem)

    result = await connection_service.connect(dongle.id)
    assert result.state == DongleState.ONLINE
    assert result.last_error is None


@pytest.mark.asyncio
async def test_connect_flushes_stale_addresses(connection_service, enrollment_service, fake_modem, fake_iface):
    """Une IPv4 périmée est purgée avant l'assignation du bearer."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    add_modem(fake_modem)
    fake_iface.set_address("wwan0", "10.95.117.182")  # adresse périmée

    result = await connection_service.connect(dongle.id)

    assert result.state == DongleState.ONLINE
    assert result.interface_name == "wwan0"
    assert await fake_iface.get_ip("wwan0") == BEARER.address


@pytest.mark.asyncio
async def test_connect_never_renames_interface(
    connection_service, enrollment_service, fake_modem, fake_iface
):
    """L'application ne renomme jamais un netdev : le nom persisté est celui
    du netdev résolu par l'adaptateur (sysfs)."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    add_modem(fake_modem)

    result = await connection_service.connect(dongle.id)

    assert result.state == DongleState.ONLINE
    assert result.interface_name == "wwan0"
    assert await fake_iface.get_ip("wwan0") == BEARER.address


@pytest.mark.asyncio
async def test_connect_modem_without_interface_fails(
    connection_service, enrollment_service, fake_modem, fake_iface
):
    """Modem trouvé mais sans port net → ERROR avec un message métier explicite."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    add_modem(fake_modem, interface=None)

    with patch.object(fake_modem, "connect", new=AsyncMock()) as mock_connect:
        result = await connection_service.connect(dongle.id)

    assert result.state == DongleState.ERROR
    mock_connect.assert_not_awaited()
    saved = await enrollment_service.dongle_repo.get_by_id(dongle.id)
    assert saved.state == DongleState.ERROR
    assert saved.last_error is not None
    assert "port net" in saved.last_error
    # Message distinct du cas « modem introuvable ».
    assert "introuvable" not in saved.last_error


@pytest.mark.asyncio
async def test_connect_ppp_bearer_fails_with_ppp_message(
    connection_service, enrollment_service, fake_modem, fake_iface
):
    """Bearer PPP sans interface réseau → message métier PPP, pas le générique."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    add_modem(
        fake_modem,
        bearer=BearerIpConfig(address="", prefix=0, method="ppp"),
    )

    service = ConnectionService(
        connection_service.dongle_repo, fake_modem, fake_iface,
        connection_service.routing, connection_service.nft,
        connection_service.connectivity,
        connection_service.allocator,
        connection_service.uplink_routes,
        ip_wait_attempts=1, ip_wait_delay=0.01,
    )
    result = await service.connect(dongle.id)

    assert result.state == DongleState.ERROR
    saved = await enrollment_service.dongle_repo.get_by_id(dongle.id)
    assert saved.state == DongleState.ERROR
    assert saved.last_error is not None
    assert "PPP" in saved.last_error
    assert "port net" in saved.last_error
    # Pas le message générique d'attente d'IPv4.
    assert "no IPv4" not in saved.last_error


@pytest.mark.asyncio
async def test_wait_for_ip_rejects_stale_address(
    connection_service, enrollment_service, fake_modem, fake_iface
):
    """L'ancienne adresse ne doit jamais valider la connexion."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    dongle.route_table_id = 10001
    dongle.fwmark = 1
    add_modem(fake_modem)
    fake_iface.set_address("wwan0", "10.95.117.182")

    service = ConnectionService(
        connection_service.dongle_repo, fake_modem, fake_iface,
        connection_service.routing, connection_service.nft,
        connection_service.connectivity,
        connection_service.allocator,
        connection_service.uplink_routes,
        ip_wait_attempts=2, ip_wait_delay=0.01,
    )
    with patch.object(fake_iface, "assign_address", new=AsyncMock()):
        result = await service.connect(dongle.id)

    assert result.state == DongleState.ERROR
    assert result.last_error is not None


@pytest.mark.asyncio
async def test_disconnect_flushes_addresses(connection_service, enrollment_service, fake_modem, fake_iface):
    """disconnect purge l'IPv4 de l'interface."""
    identity = IdentityMaterial(imei="123456789012345")
    dongle = (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle
    dongle.interface_name = "wwan0"
    add_modem(fake_modem)
    await connection_service.connect(dongle.id)
    # Aucun renommage : l'interface garde le nom du modem (wwan0).
    assert await fake_iface.get_ip("wwan0") == BEARER.address

    await connection_service.disconnect(dongle.id)

    assert await fake_iface.get_ip("wwan0") is None
