"""RealNetworkManager piloté par FakeShell avec sorties nmcli/gdbus en boîtes."""

import logging
from dataclasses import dataclass

from broxy4g.adapters.network_manager import PROFILE_METRIC, NmIpv4, RealNetworkManager
from broxy4g.adapters.network_manager_cli_output import (
    NmConnectionDetail,
    parse_active_uuids,
    parse_connection_detail,
    split_nmcli_t,
)
from broxy4g.adapters.nm_foreign_connections import (
    _ACTIVE_FIELDS,
    _DELETE_METHOD,
    _DETAIL_FIELDS,
    _LIST_FIELDS,
)
from broxy4g.adapters.shell import FakeShell, ShellResult

IFACE = "enx02360c7c147a"
MAC = "02:36:0c:7c:14:7a"
NAME = "broxy4g-phone-ABC123"

# Identité et chemins D-Bus réels mesurés sur le Pi (client 1.52.1 / démon 1.46).
# Le champ DBUS-PATH est le **chemin** fourni par nmcli (uuid sans tirets, qui
# sont illégaux dans un chemin d'objet D-Bus) — jamais reconstruit à la main.
_OWN_UUID = "390c2b1d-b158-4d56-8181-ff668434172b"
_COMPETITOR = ("Wired connection 1", "d8007283-7734-3c67-86b8-10dcd81b79ed")
_OWN_DBUS_PATH = "/org/freedesktop/NetworkManager/Settings/390c2b1db1584d568181ff668434172b"
_COMPETITOR_DBUS_PATH = "/org/freedesktop/NetworkManager/Settings/d800728377343c6786b810dcd81b79ed"

_VERIFY_FIELDS = (
    "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,"
    "connection.autoconnect,802-3-ethernet.mac-address"
)
_VERIFY_FIELDS_IFACE = (
    "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,"
    "connection.autoconnect,connection.interface-name"
)

_COMPETITOR = ("Wired connection 1", "d8007283-0000-0000-0000-000000000000")

_DHCI4_OUT = (
    "DHCP4.IP_ADDRESS[0]:  192.168.172.78\n"
    "DHCP4.SUBNET_MASK[0]:  255.255.255.0\n"
    "DHCP4.OPTION[27]:  routers = 192.168.172.245\n"
)


def verify_cmd() -> list[str]:
    return ["nmcli", "-g", _VERIFY_FIELDS, "con", "show", NAME]


def verify_cmd_ifname() -> list[str]:
    return ["nmcli", "-g", _VERIFY_FIELDS_IFACE, "con", "show", NAME]


def device_show_cmd() -> list[str]:
    return ["nmcli", "-g", "IP4.ADDRESS", "device", "show", IFACE]


def profile_show_cmd() -> list[str]:
    return ["nmcli", "-g", "IP4.ADDRESS", "con", "show", NAME]


def verify_out(metric: int = PROFILE_METRIC, mac: str = MAC) -> str:
    return f"auto\nyes\ndisabled\n{metric}\nyes\n{mac}\n"


def verify_out_ifname(metric: int = PROFILE_METRIC, interface: str = IFACE) -> str:
    return f"auto\nyes\ndisabled\n{metric}\nyes\n{interface}\n"


def gdbus_write_cmd(name: str = NAME, mac: str = MAC, metric: int = PROFILE_METRIC) -> list[str]:
    """argv exact de l'écriture du profil par D-Bus brut (dict GVariant compris).

    Le profil est lié par MAC : setting ``802-3-ethernet`` portant
    ``mac-address`` en ``ay`` (``<[byte 0xNN, …]>``, hex minuscule), bloc
    ``connection`` sans ``mac-address`` ni ``interface-name`` (forme
    mesurée acceptée par NM — cf. ``nm_profile_payload``).
    """
    data = ", ".join(f"byte 0x{int(p, 16):02x}" for p in mac.lower().split(":"))
    variant = (
        f"{{'connection': {{'id': <'{name}'>, 'type': <'802-3-ethernet'>, "
        f"'autoconnect': <true>}}, "
        f"'802-3-ethernet': {{'mac-address': <[{data}]>}}, "
        f"'ipv4': {{'method': <'auto'>, 'never-default': <true>, "
        f"'route-metric': <{metric}>}}, 'ipv6': {{'method': <'disabled'>}}}}"
    )
    return [
        "gdbus", "call", "--system",
        "--dest", "org.freedesktop.NetworkManager",
        "--object-path", "/org/freedesktop/NetworkManager/Settings",
        "--method", "org.freedesktop.NetworkManager.Settings.AddConnection",
        variant,
    ]


def gdbus_write_cmd_ifname(name: str = NAME, interface: str = IFACE, metric: int = PROFILE_METRIC) -> list[str]:
    """Écriture en repli MAC illisible : liaison par ``connection.interface-name``."""
    variant = (
        f"{{'connection': {{'id': <'{name}'>, 'type': <'802-3-ethernet'>, "
        f"'interface-name': <'{interface}'>, 'autoconnect': <true>}}, "
        f"'ipv4': {{'method': <'auto'>, 'never-default': <true>, "
        f"'route-metric': <{metric}>}}, 'ipv6': {{'method': <'disabled'>}}}}"
    )
    return [
        "gdbus", "call", "--system",
        "--dest", "org.freedesktop.NetworkManager",
        "--object-path", "/org/freedesktop/NetworkManager/Settings",
        "--method", "org.freedesktop.NetworkManager.Settings.AddConnection",
        variant,
    ]


def foreign_own_uuid_cmd() -> list[str]:
    return ["nmcli", "-g", "connection.uuid", "con", "show", NAME]


def foreign_list_cmd() -> list[str]:
    return ["nmcli", "-t", "-f", _LIST_FIELDS, "con", "show"]


def foreign_active_cmd() -> list[str]:
    return ["nmcli", "-t", "-f", _ACTIVE_FIELDS, "con", "show", "--active"]


def foreign_detail_cmd(uuid: str) -> list[str]:
    return ["nmcli", "-g", _DETAIL_FIELDS, "con", "show", uuid]


def foreign_delete_cmd(dbus_path: str) -> list[str]:
    """Suppression D-Bus sur le ``DBUS-PATH`` fourni par nmcli (jamais reconstruit)."""
    return [
        "gdbus", "call", "--system",
        "--dest", "org.freedesktop.NetworkManager",
        "--object-path", dbus_path,
        "--method", _DELETE_METHOD,
    ]


def detail_output(iface: str = "", uuid: str = "", mac: str = "") -> str:
    """Sortie ``nmcli -g connection.interface-name,connection.uuid,…mac-address`` (une par ligne)."""
    return f"{iface}\n{uuid}\n{mac}\n"


def foreign_listing(
    own: tuple[str, str] = (NAME, _OWN_UUID),
    competitor: tuple[str, str] | None = _COMPETITOR,
    own_path: str = _OWN_DBUS_PATH,
    competitor_path: str = _COMPETITOR_DBUS_PATH,
) -> str:
    """Énumération ``nmcli -t -f NAME,UUID,TYPE,DBUS-PATH`` : colonnes ``:`` (jamais de
    tabulation), champs vides tolérés. Notre profil + connexions réelles du Pi
    (bridge/loopback non candidates) + la connexion étrangère concurrente."""
    lines = [
        f"{own[0]}:{own[1]}:802-3-ethernet:{own_path}",
        "br-37221a02879e:16ecd540-89d6-494e-a924-1fd26e84d481:bridge:"
        "/org/freedesktop/NetworkManager/Settings/16ecd54089d6494ea9241fd26e84d481",
        "docker0:8aeffba2-20a7-46b5-98b7-6837f34277d7:bridge:"
        "/org/freedesktop/NetworkManager/Settings/8aeffba220a746b598b76837f34277d7",
        "lo:3da713cc-1a77-403b-8077-2bc7a4a7113e:loopback:"
        "/org/freedesktop/NetworkManager/Settings/3da713cc1a77403b80772bc7a4a7113e",
    ]
    if competitor is not None:
        lines.append(f"{competitor[0]}:{competitor[1]}:802-3-ethernet:{competitor_path}")
    return "".join(line + "\n" for line in lines)


def foreign_active_listing(include_competitor: bool = False) -> str:
    """``nmcli -t -f UUID,DEVICE con show --active`` : notre profil actif (+ autres),
    la concurrente est **inactive** (mesuré : ACTIVE=no) sauf indicé."""
    lines = [
        f"{_OWN_UUID}:{IFACE}",
        "16ecd540-89d6-494e-a924-1fd26e84d481:br-37221a02879e",
        "8aeffba2-20a7-46b5-98b7-6837f34277d7:docker0",
        "3da713cc-1a77-403b-8077-2bc7a4a7113e:lo",
    ]
    if include_competitor:
        lines.append(f"{_COMPETITOR[1]}:{IFACE}")
    return "".join(line + "\n" for line in lines)


@dataclass
class _Conn:
    """Une connexion NM du store mémoire (enum/détail/active/Delete cohérents)."""

    name: str
    uuid: str
    type: str
    dbus_path: str
    iface: str
    mac: str
    active: bool = False


class NmStoreShell(FakeShell):
    """FakeShell **état** : énumération, détail, active et ``Delete`` mutent le store.

    Permet de prouver l'idempotence (2ᵉ appel sans mutation) et le no-op quand la
    concurrente est déjà absente — impossible avec des réponses statiques.
    """

    def __init__(self, own_name: str, own_uuid: str, conns: list[_Conn]):
        super().__init__()
        self.calls: list[list[str]] = []
        self._own_name = own_name
        self._own_uuid = own_uuid
        self._conns: dict[str, _Conn] = {c.uuid: c for c in conns}

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        self.calls.append(list(command))
        if command == foreign_own_uuid_cmd() and self._own_name == NAME:
            return ShellResult(0, self._own_uuid + "\n", "")
        if command == foreign_list_cmd():
            lines = [f"{c.name}:{c.uuid}:{c.type}:{c.dbus_path}" for c in self._conns.values()]
            return ShellResult(0, "".join(line + "\n" for line in lines), "")
        if command == foreign_active_cmd():
            lines = [f"{c.uuid}:{c.iface}" for c in self._conns.values() if c.active]
            return ShellResult(0, "".join(line + "\n" for line in lines), "")
        if (
            len(command) == 6
            and command[:3] == ["nmcli", "-g", _DETAIL_FIELDS]
            and command[3:5] == ["con", "show"]
        ):
            conn = self._conns.get(command[5])
            if conn is None:
                return ShellResult(1, "", "Error: connection not found")
            return ShellResult(0, detail_output(conn.iface, conn.uuid, conn.mac), "")
        if command[:2] == ["gdbus", "call"] and command[5] == "--object-path" and command[7] == "--method":
            if command[8] == _DELETE_METHOD:
                for uuid, conn in list(self._conns.items()):
                    if conn.dbus_path == command[6]:
                        del self._conns[uuid]
                        break
            return ShellResult(0, "(", "")
        return ShellResult(1, "", "Command not found")


class RecordingShell(FakeShell):
    """FakeShell qui journalise chaque commande émise."""

    def __init__(self):
        super().__init__()
        self.calls: list[list[str]] = []

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        self.calls.append(list(command))
        return await super().run(command, timeout)


class StaleShell(RecordingShell):
    """Profil présent mais périmé (autre MAC) : la 1re relecture -g ne passe pas."""

    def __init__(self):
        super().__init__()
        self._deleted = False

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        result = await super().run(command, timeout)
        if command == verify_cmd() and not self._deleted:
            return ShellResult(0, verify_out(mac="02:36:0c:7c:14:7b"), "")
        if command[1:3] == ["con", "delete"] and result.returncode == 0:
            self._deleted = True
        return result


def make_shell(profile_present: bool = False, mac: str | None = MAC) -> RecordingShell:
    shell = RecordingShell()
    shell.set_response(
        ["nmcli", "con", "show", NAME],
        ShellResult(0, f"{NAME}\n", "") if profile_present else ShellResult(1, "", "Error: connection not found"),
    )
    if mac is not None:
        shell.set_response(verify_cmd(), ShellResult(0, verify_out(mac=mac), ""))
        shell.set_response(
            gdbus_write_cmd(mac=mac),
            ShellResult(0, "(objectpath '/org/freedesktop/NetworkManager/Settings/5',)\n", ""),
        )
    else:
        shell.set_response(verify_cmd_ifname(), ShellResult(0, verify_out_ifname(), ""))
        shell.set_response(
            gdbus_write_cmd_ifname(),
            ShellResult(0, "(objectpath '/org/freedesktop/NetworkManager/Settings/5',)\n", ""),
        )
    return shell


async def test_device_ipv4_parses_address_with_prefix():
    shell = RecordingShell()
    shell.set_response(
        ["nmcli", "-g", "IP4.ADDRESS", "device", "show", IFACE],
        ShellResult(0, "192.168.172.78/24\n", ""),
    )

    assert await RealNetworkManager(shell).device_ipv4(IFACE) == NmIpv4("192.168.172.78", 24)


async def test_device_ipv4_multi_address_pipe_no_exception():
    """Non-régression du défaut mesuré (Pi) : la sortie
    ``192.168.172.79/24 | 192.168.172.78/24`` ne doit JAMAIS lever —
    l'ancienne lecture faisait ``int("24 | 192.168.172.78/24")`` → ``ValueError``.
    Première adresse imprimée renvoyée (l'ordre du noyau n'est pas fiable).
    """
    shell = RecordingShell()
    shell.set_response(
        device_show_cmd(),
        ShellResult(0, "192.168.172.79/24 | 192.168.172.78/24\n", ""),
    )

    assert await RealNetworkManager(shell).device_ipv4(IFACE) == NmIpv4("192.168.172.79", 24)


async def test_device_ipv4_multi_address_variable_spacing():
    shell = RecordingShell()
    shell.set_response(
        device_show_cmd(),
        ShellResult(0, "  192.168.172.79/24  |   192.168.172.78/24 \n", ""),
    )

    assert await RealNetworkManager(shell).device_ipv4(IFACE) == NmIpv4("192.168.172.79", 24)


async def test_device_ipv4_three_addresses_first_wins():
    shell = RecordingShell()
    shell.set_response(
        device_show_cmd(),
        ShellResult(0, "10.9.0.2/24 | 192.168.172.79/24 | 192.168.172.78/24\n", ""),
    )

    assert await RealNetworkManager(shell).device_ipv4(IFACE) == NmIpv4("10.9.0.2", 24)


async def test_device_ipv4_tolerates_missing_prefix():
    shell = RecordingShell()
    shell.set_response(
        device_show_cmd(),
        ShellResult(0, "192.168.172.78\n", ""),
    )

    assert await RealNetworkManager(shell).device_ipv4(IFACE) == NmIpv4("192.168.172.78", 0)


async def test_device_ipv4_multi_address_missing_prefix_first():
    shell = RecordingShell()
    shell.set_response(
        device_show_cmd(),
        ShellResult(0, "192.168.172.78 | 10.0.0.2/16\n", ""),
    )

    assert await RealNetworkManager(shell).device_ipv4(IFACE) == NmIpv4("192.168.172.78", 0)


async def test_device_ipv4_empty_output_none():
    shell = RecordingShell()
    shell.set_response(device_show_cmd(), ShellResult(0, "", ""))

    assert await RealNetworkManager(shell).device_ipv4(IFACE) is None


async def test_device_ipv4_garbage_output_none_without_exception():
    shell = RecordingShell()
    shell.set_response(device_show_cmd(), ShellResult(0, "abc\n", ""))

    assert await RealNetworkManager(shell).device_ipv4(IFACE) is None


async def test_device_ipv4_none_when_device_missing():
    shell = RecordingShell()

    assert await RealNetworkManager(shell).device_ipv4(IFACE) is None


async def test_profile_ipv4_exact_argv_first_address():
    """argv exact (même style que ``dhcp_gateway``) ; même sortie mesurée multi-adresses."""
    shell = RecordingShell()
    shell.set_response(
        profile_show_cmd(),
        ShellResult(0, "192.168.172.79/24 | 192.168.172.78/24\n", ""),
    )

    manager = RealNetworkManager(shell)
    assert await manager.profile_ipv4(NAME) == NmIpv4("192.168.172.79", 24)
    assert shell.calls == [profile_show_cmd()]


async def test_profile_ipv4_none_when_rc_nonzero():
    shell = RecordingShell()
    shell.set_response(
        profile_show_cmd(), ShellResult(1, "", "Error: connection not found")
    )

    assert await RealNetworkManager(shell).profile_ipv4(NAME) is None


async def test_dhcp_gateway_extracts_routers_option():
    shell = RecordingShell()
    shell.set_response(["nmcli", "-f", "DHCP4", "con", "show", NAME], ShellResult(0, _DHCI4_OUT, ""))

    assert await RealNetworkManager(shell).dhcp_gateway(NAME) == "192.168.172.245"


async def test_dhcp_gateway_none_without_routers():
    shell = RecordingShell()
    shell.set_response(["nmcli", "-f", "DHCP4", "con", "show", NAME], ShellResult(0, "", ""))

    assert await RealNetworkManager(shell).dhcp_gateway(NAME) is None


async def test_ensure_profile_writes_via_gdbus_when_absent():
    shell = make_shell(profile_present=False)

    assert await RealNetworkManager(shell).ensure_profile(IFACE, MAC, NAME) is True

    writes = [c for c in shell.calls if c[0] == "gdbus"]
    assert len(writes) == 1  # une seule écriture
    assert writes[0] == gdbus_write_cmd()  # argv exact, dict GVariant compris
    variant = writes[0][-1]
    # Liaison par identité matérielle : setting dédié, type ay, hex minuscule.
    assert (
        "'802-3-ethernet': {'mac-address': "
        "<[byte 0x02, byte 0x36, byte 0x0c, byte 0x7c, byte 0x14, byte 0x7a]>}" in variant
    )
    connection_block = variant.split(", '802-3-ethernet':")[0]
    assert "mac-address" not in connection_block  # jamais dans le bloc connection (NM : unknown property)
    assert "'interface-name'" not in variant  # jamais lié au nom d'interface
    for command in shell.calls:  # plus aucun con add / con mod (cassé par le décalage de versions)
        assert command[1:3] not in (["con", "add"], ["con", "mod"])


async def test_ensure_profile_noop_when_present_and_conforming():
    shell = make_shell(profile_present=True)

    assert await RealNetworkManager(shell).ensure_profile(IFACE, MAC, NAME) is True

    assert shell.calls == [  # aucune écriture, aucun delete, aucun gdbus
        ["nmcli", "con", "show", NAME],
        verify_cmd(),
    ]


async def test_ensure_profile_deletes_then_rewrites_when_stale():
    shell = StaleShell()
    shell.set_response(["nmcli", "con", "show", NAME], ShellResult(0, f"{NAME}\n", ""))
    shell.set_response(verify_cmd(), ShellResult(0, verify_out(), ""))
    shell.set_response(["nmcli", "con", "delete", NAME], ShellResult(0, "", ""))
    shell.set_response(gdbus_write_cmd(), ShellResult(0, "(objectpath '/org/freedesktop/NetworkManager/Settings/5',)\n", ""))

    assert await RealNetworkManager(shell).ensure_profile(IFACE, MAC, NAME) is True

    deletes = [c for c in shell.calls if c[1:3] == ["con", "delete"]]
    writes = [c for c in shell.calls if c[0] == "gdbus"]
    assert deletes == [["nmcli", "con", "delete", NAME]]
    assert writes == [gdbus_write_cmd()]
    assert shell.calls.index(deletes[0]) < shell.calls.index(writes[0])  # delete avant écriture


async def test_ensure_profile_verifies_mac_not_interface_name():
    """Un profil sans la MAC attendue (profil périmé ou pré-MAC) est refusé :
    la vérification porte sur la MAC, jamais sur ``connection.interface-name``."""
    shell = make_shell(profile_present=True)
    shell.set_response(verify_cmd(), ShellResult(0, "auto\nyes\ndisabled\n21000\nyes\n", ""))
    shell.set_response(["nmcli", "con", "delete", NAME], ShellResult(0, "", ""))
    shell.set_response(gdbus_write_cmd(), ShellResult(0, "(objectpath '/org/freedesktop/NetworkManager/Settings/5',)\n", ""))

    # La 2e vérification (post-écriture) renvoie la même sortie : échec définitif.
    assert await RealNetworkManager(shell).ensure_profile(IFACE, MAC, NAME) is False

    assert shell.calls == [
        ["nmcli", "con", "show", NAME],
        verify_cmd(),
        ["nmcli", "con", "delete", NAME],
        gdbus_write_cmd(),
        verify_cmd(),
    ]


async def test_ensure_profile_falls_back_to_interface_name_when_mac_unreadable(caplog):
    """MAC illisible : repli tolérant sur le comportement antérieur + warning,
    jamais d'exception."""
    shell = make_shell(profile_present=False, mac=None)
    with caplog.at_level(logging.WARNING):
        assert await RealNetworkManager(shell).ensure_profile(IFACE, None, NAME) is True

    writes = [c for c in shell.calls if c[0] == "gdbus"]
    assert writes == [gdbus_write_cmd_ifname()]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert IFACE in warnings[0].getMessage()


async def test_ensure_profile_falls_back_when_mac_invalid(caplog):
    """(d) MAC non None mais non convertible en 6 octets hex → repli sur
    interface-name + warning, jamais d'exception ni de profil refusé par NM."""
    shell = make_shell(profile_present=False, mac=None)  # réponses du repli ifname
    with caplog.at_level(logging.WARNING):
        assert await RealNetworkManager(shell).ensure_profile(IFACE, "zz:36:0c:7c:14:7a", NAME) is True

    writes = [c for c in shell.calls if c[0] == "gdbus"]
    assert writes == [gdbus_write_cmd_ifname()]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "zz:36:0c:7c:14:7a" in warnings[0].getMessage()


def nmcli_escaped_mac(value: str = MAC) -> str:
    """Sortie terse mesurée de ``nmcli -g 802-3-ethernet.mac-address`` :
    majuscules + ``:`` échappés d'un antislash."""
    return value.upper().replace(":", "\\:")


async def test_verify_profile_conforming_with_escaped_uppercase_mac():
    """(e) Relecture mesurée ``02\\:36\\:0C\\:7C\\:14\\:7A`` → conforme : profil
    présent, aucune écriture, aucune suppression (normalisation antislash + casse)."""
    shell = make_shell(profile_present=True)
    shell.set_response(
        verify_cmd(),
        ShellResult(0, f"auto\nyes\ndisabled\n{PROFILE_METRIC}\nyes\n{nmcli_escaped_mac()}\n", ""),
    )

    assert await RealNetworkManager(shell).ensure_profile(IFACE, MAC, NAME) is True
    assert shell.calls == [["nmcli", "con", "show", NAME], verify_cmd()]


async def test_ensure_profile_deletes_written_profile_on_positive_wrong_identity(caplog):
    """(e) Fail-closed : écriture réussie mais identité lue **positive et fausse**
    (autre MAC) → suppression du profil juste écrit (activable sur n'importe
    quelle interface), retour False."""
    shell = make_shell(profile_present=False)
    shell.set_response(verify_cmd(), ShellResult(0, verify_out(mac="02:36:0c:7c:99:99"), ""))
    shell.set_response(["nmcli", "con", "delete", NAME], ShellResult(0, "", ""))
    with caplog.at_level(logging.WARNING):
        assert await RealNetworkManager(shell).ensure_profile(IFACE, MAC, NAME) is False

    assert shell.calls == [
        ["nmcli", "con", "show", NAME],  # profil absent : pas de vérification préalable
        gdbus_write_cmd(),
        verify_cmd(),  # identité positive et fausse (autre MAC)
        ["nmcli", "con", "delete", NAME],  # fail-closed : suppression du profil écrit
    ]
    assert any("défaut d'isolation" in r.getMessage() for r in caplog.records)


async def test_ensure_profile_no_deletion_when_verify_fails_nonconclusive():
    """(e) Échec **non concluant** post-écriture (nmcli rc ≠ 0) → False sans
    supprimer le profil (pas de destruction sur incident transitoire)."""
    shell = RecordingShell()
    shell.set_response(
        ["nmcli", "con", "show", NAME], ShellResult(1, "", "Error: connection not found"),
    )
    shell.set_response(verify_cmd(), ShellResult(1, "", "Error: connection not found"))
    shell.set_response(
        gdbus_write_cmd(), ShellResult(0, "(objectpath '/org/freedesktop/NetworkManager/Settings/5',)\n", ""),
    )

    assert await RealNetworkManager(shell).ensure_profile(IFACE, MAC, NAME) is False
    assert [c for c in shell.calls if c[1:3] == ["con", "delete"]] == []


async def test_ensure_profile_no_deletion_when_non_identity_field_wrong():
    """(e) Champ non-identité faux (metric) avec identité correcte → non concluant :
    False sans suppression du profil."""
    shell = make_shell(profile_present=False)
    shell.set_response(verify_cmd(), ShellResult(0, f"auto\nyes\ndisabled\n1\nyes\n{MAC}\n", ""))

    assert await RealNetworkManager(shell).ensure_profile(IFACE, MAC, NAME) is False
    assert [c for c in shell.calls if c[1:3] == ["con", "delete"]] == []


async def test_ensure_profile_written_settings_never_default_and_ipv6_disabled():
    shell = make_shell(profile_present=False)
    await RealNetworkManager(shell).ensure_profile(IFACE, MAC, NAME)
    variant = [c for c in shell.calls if c[0] == "gdbus"][0][-1]
    assert "'never-default': <true>" in variant
    assert f"'route-metric': <{PROFILE_METRIC}>" in variant
    assert "'ipv6': {'method': <'disabled'>}}" in variant


async def test_ensure_profile_false_when_settings_wrong():
    shell = make_shell(profile_present=True)
    shell.set_response(verify_cmd(), ShellResult(0, verify_out(mac="02:36:0c:7c:99:99"), ""))
    shell.set_response(["nmcli", "con", "delete", NAME], ShellResult(0, "", ""))
    shell.set_response(gdbus_write_cmd(), ShellResult(0, "(objectpath '/org/freedesktop/NetworkManager/Settings/5',)\n", ""))

    assert await RealNetworkManager(shell).ensure_profile(IFACE, MAC, NAME) is False


async def test_missing_nmcli_returns_false_and_warns_once(caplog):
    """rc 127 (binaire absent) → False partout + un seul WARNING par instance."""
    shell = RecordingShell()
    shell.set_response(
        ["nmcli", "con", "show", NAME],
        ShellResult(returncode=127, stdout="", stderr="command not found: nmcli"),
    )
    manager = RealNetworkManager(shell)
    with caplog.at_level(logging.WARNING):
        assert await manager.profile_exists(NAME) is False
        assert await manager.ensure_profile(IFACE, MAC, NAME) is False
        assert await manager.up(NAME) is False
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "nmcli" in message
    assert "command not found: nmcli" in message  # extrait de stderr
    assert "network-manager" in message  # paquet à installer dans l'image Docker


async def test_missing_gdbus_returns_false_and_warns_once(caplog):
    """gdbus rc 127 (binaire absent) → False + un seul WARNING par instance nommant gdbus."""
    shell = RecordingShell()
    shell.set_response(["nmcli", "con", "show", NAME], ShellResult(1, "", "Error: connection not found"))
    shell.set_response(gdbus_write_cmd(), ShellResult(127, "", "command not found: gdbus"))
    manager = RealNetworkManager(shell)
    with caplog.at_level(logging.WARNING):
        assert await manager.ensure_profile(IFACE, MAC, NAME) is False
        assert await manager.ensure_profile(IFACE, MAC, NAME) is False
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "gdbus" in message
    assert "command not found: gdbus" in message  # extrait de stderr
    assert "libglib2.0-bin" in message  # paquet à installer dans l'image Docker


def make_foreign_shell(
    detail_iface: str = IFACE,
    detail_mac: str = "",
    competitor: tuple[str, str] | None = _COMPETITOR,
    own_uuid: str | None = _OWN_UUID,
    delete_rc: int = 0,
    list_rc: int = 0,
    list_stderr: str = "",
    include_competitor_active: bool = False,
) -> RecordingShell:
    """FakeShell standard du flux de suppression : enum, détail, active, Delete."""
    shell = RecordingShell()
    if own_uuid is None:
        shell.set_response(foreign_own_uuid_cmd(), ShellResult(1, "", "Error: connection not found"))
    else:
        shell.set_response(foreign_own_uuid_cmd(), ShellResult(0, own_uuid + "\n", ""))
    shell.set_response(foreign_list_cmd(), ShellResult(list_rc, foreign_listing(competitor=competitor), list_stderr))
    shell.set_response(foreign_active_cmd(), ShellResult(0, foreign_active_listing(include_competitor_active), ""))
    if competitor is not None:
        shell.set_response(
            foreign_detail_cmd(competitor[1]),
            ShellResult(0, detail_output(detail_iface, competitor[1], detail_mac), ""),
        )
        shell.set_response(foreign_delete_cmd(_COMPETITOR_DBUS_PATH), ShellResult(delete_rc, "(", ""))
    return shell


async def test_delete_foreign_by_interface_real_listing(caplog):
    """Énumération réelle du Pi (colonnes ``:`` jamais de tabulation) : la concurrente
    ``Wired connection 1`` (interface-name == notre interface) est supprimée par D-Bus
    sur son ``DBUS-PATH`` ; les bridge/loopback ne sont jamais touchées ; une seule
    ligne ACTION ADMIN avec nom + uuid + interface."""
    shell = make_foreign_shell(detail_iface=IFACE, detail_mac="")
    with caplog.at_level(logging.WARNING):
        await RealNetworkManager(shell).remove_foreign_connections(IFACE, MAC, NAME)

    deletes = [c for c in shell.calls if c[0] == "gdbus"]
    assert deletes == [foreign_delete_cmd(_COMPETITOR_DBUS_PATH)]
    path = deletes[0][6]
    assert path == _COMPETITOR_DBUS_PATH  # chemin fourni par nmcli, sans tirets
    assert _COMPETITOR[1] not in path  # l'uuid à tirets n'apparaît jamais (invalide en D-Bus path)
    for command in shell.calls:  # jamais d'écriture partielle ni de con delete par nom
        assert command[1:3] != ["con", "mod"]
        assert command[1:3] != ["con", "delete"]
    admin = [r for r in caplog.records if r.getMessage().startswith("ACTION ADMIN :")]
    assert len(admin) == 1
    message = admin[0].getMessage()
    assert _COMPETITOR[0] in message and _COMPETITOR[1] in message and IFACE in message


async def test_delete_foreign_matches_by_mac_when_unbound_by_name():
    """Concurrente non épinglée par interface mais au même MAC : supprimée aussi."""
    shell = make_foreign_shell(detail_iface="", detail_mac=MAC)
    await RealNetworkManager(shell).remove_foreign_connections(IFACE, MAC, NAME)
    assert [c for c in shell.calls if c[0] == "gdbus"] == [foreign_delete_cmd(_COMPETITOR_DBUS_PATH)]


async def test_delete_foreign_idempotent_no_second_mutation():
    """Store cohérent : la 1re passe supprime la concurrente ; la 2ᵉ passe, la
    concurrente étant absente de l'énumération, ne produit **aucune** mutation."""
    shell = NmStoreShell(NAME, _OWN_UUID, [
        _Conn(NAME, _OWN_UUID, "802-3-ethernet", _OWN_DBUS_PATH, IFACE, MAC, active=True),
        _Conn(_COMPETITOR[0], _COMPETITOR[1], "802-3-ethernet", _COMPETITOR_DBUS_PATH, IFACE, "", active=False),
    ])
    nm = RealNetworkManager(shell)

    await nm.remove_foreign_connections(IFACE, MAC, NAME)
    assert [c for c in shell.calls if c[0] == "gdbus"] == [foreign_delete_cmd(_COMPETITOR_DBUS_PATH)]
    assert _COMPETITOR[1] not in shell._conns  # retirée du store par la suppression

    shell.calls = []
    await nm.remove_foreign_connections(IFACE, MAC, NAME)
    assert [c for c in shell.calls if c[0] == "gdbus"] == []  # 2ᵉ appel : aucun gdbus


async def test_delete_foreign_noop_when_only_own_profile():
    """Seule notre connexion présente : aucune suppression (uuid == notre profil),
    et le détail de notre propre profil n'est même pas consulté."""
    shell = make_foreign_shell(competitor=None)
    await RealNetworkManager(shell).remove_foreign_connections(IFACE, MAC, NAME)
    assert [c for c in shell.calls if c[0] == "gdbus"] == []
    assert all(c != foreign_detail_cmd(_OWN_UUID) for c in shell.calls)


async def test_delete_foreign_fail_safe_when_own_uuid_unknown(caplog):
    """uuid de notre profil introuvable → **aucune** suppression (on ne peut plus
    distinguer notre propre connexion), avertissement fail-safe, pas d'exception,
    arrêt avant toute énumération."""
    shell = make_foreign_shell(own_uuid=None)
    with caplog.at_level(logging.WARNING):
        await RealNetworkManager(shell).remove_foreign_connections(IFACE, MAC, NAME)

    assert shell.calls == [foreign_own_uuid_cmd()]
    assert [c for c in shell.calls if c[0] == "gdbus"] == []
    assert any("fail-safe" in r.getMessage() for r in caplog.records)


async def test_delete_foreign_ignores_active_connection(caplog):
    """Concurrente ACTIVE (mesuré : notre profil est actif) → ignorée, jamais
    supprimée, avertissement ACTION ADMIN explicite."""
    shell = make_foreign_shell(detail_iface=IFACE, include_competitor_active=True)
    with caplog.at_level(logging.WARNING):
        await RealNetworkManager(shell).remove_foreign_connections(IFACE, MAC, NAME)

    assert [c for c in shell.calls if c[0] == "gdbus"] == []
    ignored = [r for r in caplog.records if "ignorée" in r.getMessage()]
    assert len(ignored) == 1
    assert ignored[0].getMessage().startswith("ACTION ADMIN :")
    assert _COMPETITOR[0] in ignored[0].getMessage()


async def test_delete_foreign_tolerates_invalid_field_error_without_exception(caplog):
    """Sortie d'erreur réelle ``invalid field 'IFNAME'`` (rc≠0) → avertissement,
    **aucune** action, aucune exception (arrêt avant toute consultation active)."""
    shell = make_foreign_shell(list_rc=1, list_stderr="Error: invalid field 'IFNAME'")
    with caplog.at_level(logging.WARNING):
        await RealNetworkManager(shell).remove_foreign_connections(IFACE, MAC, NAME)  # pas de raise

    assert shell.calls == [foreign_own_uuid_cmd(), foreign_list_cmd()]
    assert [c for c in shell.calls if c[0] == "gdbus"] == []
    assert any("lecture des connexions NM impossible" in r.getMessage() for r in caplog.records)


async def test_delete_foreign_tolerates_delete_failure_without_exception(caplog):
    """Suppression D-Bus en échec → warning (en échec), aucune exception propagée."""
    shell = make_foreign_shell(detail_iface=IFACE, delete_rc=1)
    with caplog.at_level(logging.WARNING):
        await RealNetworkManager(shell).remove_foreign_connections(IFACE, MAC, NAME)

    failures = [r for r in caplog.records if "en échec" in r.getMessage()]
    assert len(failures) == 1
    assert _COMPETITOR[0] in failures[0].getMessage()
    assert _COMPETITOR[1] in failures[0].getMessage()


_REAL_ACTIVE_LINE = f"Wired connection 1:{_COMPETITOR[1]}:802-3-ethernet:::no"


def test_split_nmcli_t_parses_real_pi_line_with_empty_fields():
    """Ligne réelle du Pi (colonnes ``:``, DEVICE et STATE vides) : scission correcte,
    séparateur tabulé **non** requis, champs vides tolérés."""
    assert split_nmcli_t(_REAL_ACTIVE_LINE) == [
        "Wired connection 1", _COMPETITOR[1], "802-3-ethernet", "", "", "no",
    ]


def test_split_nmcli_t_tolerates_escape_and_empty_line():
    assert split_nmcli_t("a\\:b:c") == ["a:b", "c"]
    assert split_nmcli_t("") == [""]


def test_parse_connection_detail_real_measured():
    """Détail réel : interface, uuid, et MAC vide tant que le correctif n'est pas déployé."""
    assert parse_connection_detail(f"{IFACE}\n{_COMPETITOR[1]}\n") == NmConnectionDetail(
        interface_name=IFACE, uuid=_COMPETITOR[1], mac=""
    )
    # MAC vide et interface vide (profil lié par MAC non déployé) → toléré
    assert parse_connection_detail("\n") == NmConnectionDetail(interface_name="", uuid="", mac="")


def test_parse_active_uuids_real_listing():
    out = f"{_OWN_UUID}:{IFACE}\n8aeffba2-20a7-46b5-98b7-6837f34277d7:docker0\n"
    assert parse_active_uuids(out) == {_OWN_UUID, "8aeffba2-20a7-46b5-98b7-6837f34277d7"}
    assert parse_active_uuids("") == set()
    assert parse_active_uuids("\n") == set()
