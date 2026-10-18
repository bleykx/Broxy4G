"""SmartphoneUplinkService : fakes ADB / NetworkManager / Interface / Routing + sysfs tmp."""

import logging

import pytest

from broxy4g.adapters.adb import FakeAdb
from broxy4g.adapters.modem_manager import BearerIpConfig
from broxy4g.adapters.net_interface import FakeInterface
from broxy4g.adapters.network_manager import FakeNetworkManager, NmIpv4
from broxy4g.adapters.routing import FakeRouting, RoutingRoute
from broxy4g.services.smartphone_uplink import SmartphoneUplinkError, SmartphoneUplinkService

SERIAL = "ABC123"
IFACE = "enx02360c7c147a"
MAC = "02:36:0c:7c:14:7a"
PROFILE = f"broxy4g-phone-{SERIAL}"


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


def make_service(tmp_path, adb=None, nm=None, routing=None, iface=None):
    return SmartphoneUplinkService(
        adb or FakeAdb(),
        nm or FakeNetworkManager(),
        iface or FakeInterface(),
        routing or FakeRouting(),
        sysfs_root=make_sysfs(tmp_path),
        iface_wait_attempts=2,
        iface_wait_delay=0.0,
        dhcp_wait_attempts=2,
        dhcp_wait_delay=0.0,
    )


async def test_bring_up_tethering_refused_raises(tmp_path):
    adb = FakeAdb()
    adb.fail_enable_times = 3  # tous les essais échouent
    service = make_service(tmp_path, adb=adb)

    with pytest.raises(SmartphoneUplinkError, match="partage de connexion USB impossible"):
        await service.bring_up(SERIAL)


async def test_bring_up_nominal_returns_bearer_dhcp(tmp_path):
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4("192.168.172.78", 24)
    nm.gateways[PROFILE] = "192.168.172.245"
    iface = FakeInterface()
    iface.set_addresses(IFACE, [("192.168.172.78", 24, False)])  # vérité noyau attendue
    iface.set_mac(IFACE, MAC)  # MAC lue dans sysfs → profil lié par identité matérielle
    service = make_service(tmp_path, nm=nm, iface=iface)

    bearer = await service.bring_up(SERIAL)

    assert bearer == BearerIpConfig(
        address="192.168.172.78", prefix=24, gateway="192.168.172.245", method="dhcp"
    )
    assert ["ensure_profile", IFACE, MAC, PROFILE, "21000"] in nm.calls
    # Suppression des connexions NM étrangères, appelée juste après
    # ensure_profile (avant la mise en ligne du profil).
    assert ["remove_foreign", IFACE, MAC, PROFILE] in nm.calls
    assert nm.calls.index(["remove_foreign", IFACE, MAC, PROFILE]) > nm.calls.index(
        ["ensure_profile", IFACE, MAC, PROFILE, "21000"]
    )
    assert ["up", PROFILE] in nm.calls


async def test_bring_up_falls_back_to_interface_binding_when_mac_unreadable(tmp_path):
    """MAC illisible (aucune entrée sysfs) : ensure_profile reçoit ``None``
    (repli sur le nom d'interface côté adaptateur), jamais d'exception."""
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4("192.168.172.78", 24)
    iface = FakeInterface()  # pas de MAC
    iface.set_addresses(IFACE, [("192.168.172.78", 24, False)])
    service = make_service(tmp_path, nm=nm, iface=iface)

    bearer = await service.bring_up(SERIAL)

    assert bearer is not None
    assert ["ensure_profile", IFACE, None, PROFILE, "21000"] in nm.calls
    assert ["remove_foreign", IFACE, None, PROFILE] in nm.calls


async def test_bring_up_no_interface_raises(tmp_path):
    service = make_service(tmp_path)  # sysfs sans aucun serial

    with pytest.raises(SmartphoneUplinkError, match="introuvable"):
        await service.bring_up("UNKNOWN")


async def test_bring_up_removes_rogue_main_default_route(tmp_path, caplog):
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4("192.168.172.78", 24)
    routing = FakeRouting()
    routing._default_routes.append(RoutingRoute(254, "default", "192.168.172.245", IFACE))
    service = make_service(tmp_path, nm=nm, routing=routing)

    with caplog.at_level(logging.WARNING):
        await service.bring_up(SERIAL)  # doit réussir, pas lever

    assert await routing.list_default_routes() == []
    assert any(
        record.levelno == logging.WARNING and IFACE in record.message
        for record in caplog.records
    )


async def test_guard_default_route_still_present_raises(tmp_path):
    class StubbornRouting(FakeRouting):
        async def remove_default_route(self, dev: str) -> bool:
            return False  # la route revient toujours

    routing = StubbornRouting()
    routing._default_routes.append(RoutingRoute(254, "default", "192.168.172.245", IFACE))
    service = make_service(tmp_path, routing=routing)

    with pytest.raises(SmartphoneUplinkError, match="route par défaut de l'hôte détournée"):
        await service.guard_default_route(IFACE)


async def test_bring_down_stops_profile_and_tethering(tmp_path):
    nm = FakeNetworkManager()
    adb = FakeAdb()
    service = make_service(tmp_path, adb=adb, nm=nm)
    await service.nm.ensure_profile(IFACE, MAC, PROFILE)

    await service.bring_down(SERIAL)

    assert ["down", PROFILE] in nm.calls
    assert ["svc", "usb", "setFunctions", "mtp"] in adb.calls


async def test_bring_down_never_raises_on_failures(tmp_path):
    nm = FakeNetworkManager()
    adb = FakeAdb()
    service = make_service(tmp_path, adb=adb, nm=nm)
    nm.up_result = False

    await service.bring_down(SERIAL)  # profil inconnu : nm.down False, pas d'exception


async def test_current_bearer_none_when_profile_has_no_address(tmp_path):
    """Fail-closed : une adresse sur le device (autre ip-config) ne fabrique
    JAMAIS de bearer — l'appelant force alors le plancher."""
    nm = FakeNetworkManager()
    nm.devices[IFACE] = NmIpv4("192.168.172.78", 24)  # vue device seulement
    service = make_service(tmp_path, nm=nm)

    assert await service.current_bearer(SERIAL) is None


async def test_current_bearer_reads_state_without_side_effects(tmp_path):
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4("192.168.172.78", 24)
    nm.gateways[PROFILE] = "192.168.172.245"
    adb = FakeAdb()
    iface = FakeInterface()
    iface.set_addresses(IFACE, [("192.168.172.78", 24, False)])  # vérité noyau attendue
    service = make_service(tmp_path, adb=adb, nm=nm, iface=iface)

    bearer = await service.current_bearer(SERIAL)

    assert bearer == BearerIpConfig(
        address="192.168.172.78", prefix=24, gateway="192.168.172.245", method="dhcp"
    )
    assert adb.calls == []  # aucun activation du partage USB


async def test_is_live_checks_exact_address(tmp_path):
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4("192.168.172.78", 24)
    iface = FakeInterface()
    iface.set_address(IFACE, "192.168.172.78")  # vérité noyau attendue
    service = make_service(tmp_path, nm=nm, iface=iface)

    assert await service.is_live(SERIAL, "192.168.172.78") is True
    assert await service.is_live(SERIAL, "10.0.0.2") is False


async def test_is_live_false_when_only_device_has_address(tmp_path):
    """Vrai/faux selon l'adresse du PROFIL (et non du device) : une adresse
    étrangère sur l'interface ne rend pas le uplink vivant."""
    nm = FakeNetworkManager()
    nm.devices[IFACE] = NmIpv4("192.168.172.78", 24)  # vue device seulement
    service = make_service(tmp_path, nm=nm)

    assert await service.is_live(SERIAL, "192.168.172.78") is False


async def test_bearer_uses_kernel_primary_not_nmcli_order(tmp_path):
    """Régression du défaut mesuré (Galaxy A21S, RNDIS) : le profil ``nmcli``
    renvoie ``.79`` en tête (ordre non fiable) alors que le noyau source
    réellement ``.78`` (primaire). Le bearer (donc la règle source) doit porter
    ``.78``. Sans la correction, le code renvoie ``.79`` → le test échoue."""
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4("192.168.172.79", 24)  # ce que nmcli imprime en 1er
    iface = FakeInterface()
    iface.set_addresses(
        IFACE,
        [("192.168.172.78", 24, False), ("192.168.172.79", 24, True)],
    )  # noyau : .78 primaire, .79 secondary
    service = make_service(tmp_path, nm=nm, iface=iface)

    bearer = await service.current_bearer(SERIAL)
    assert bearer is not None
    assert bearer.address == "192.168.172.78"
    assert bearer.prefix == 24


async def test_bearer_falls_back_to_nm_lease_when_kernel_unreadable(tmp_path):
    """Repli : noyau illisible (aucune adresse) → l'adresse du bail NM est utilisée
    (jamais ``None`` si le profil porte une adresse)."""
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4("192.168.172.78", 24)
    iface = FakeInterface()  # aucune adresse noyau
    service = make_service(tmp_path, nm=nm, iface=iface)

    bearer = await service.current_bearer(SERIAL)
    assert bearer is not None
    assert bearer.address == "192.168.172.78"
    assert bearer.prefix == 24


async def test_current_bearer_none_when_profile_empty_but_kernel_has_address(tmp_path):
    """Fail-closed : une adresse présente sur l'interface mais absente de notre
    profil ne fabrique jamais de bearer (elle est étrangère à notre uplink)."""
    nm = FakeNetworkManager()  # profil sans bail
    iface = FakeInterface()
    iface.set_address(IFACE, "192.168.172.78")  # adresse sur le device seulement
    service = make_service(tmp_path, nm=nm, iface=iface)

    assert await service.current_bearer(SERIAL) is None


async def test_current_bearer_none_when_interface_unresolvable(tmp_path):
    """Plus d'adresse fabriquée sans interface : sysfs sans serial → ``None``
    même si le profil porte une adresse."""
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4("192.168.172.78", 24)
    service = make_service(tmp_path, nm=nm)  # sysfs ne porte que SERIAL

    assert await service.current_bearer("UNKNOWN") is None


async def test_is_live_compares_against_kernel_address(tmp_path):
    """``is_live`` compare à l'adresse **noyau** (pas à l'ordre ``nmcli``) :
    profil ``.79``, noyau ``.78`` → vivant sur ``.78``, non vivant sur ``.79``."""
    nm = FakeNetworkManager()
    nm.profile_ips[PROFILE] = NmIpv4("192.168.172.79", 24)
    iface = FakeInterface()
    iface.set_addresses(
        IFACE,
        [("192.168.172.78", 24, False), ("192.168.172.79", 24, True)],
    )
    service = make_service(tmp_path, nm=nm, iface=iface)

    assert await service.is_live(SERIAL, "192.168.172.78") is True
    assert await service.is_live(SERIAL, "192.168.172.79") is False


async def test_profile_name_sanitized(tmp_path):
    service = make_service(tmp_path)

    assert service.profile_name("ABC-123_456.789") == "broxy4g-phone-ABC-123_456.789"
    assert service.profile_name("AB C/x") == "broxy4g-phone-AB_C_x"
