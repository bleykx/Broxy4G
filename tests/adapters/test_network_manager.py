"""RealNetworkManager piloté par FakeShell avec sorties nmcli/gdbus en boîtes."""

import logging

from broxy4g.adapters.network_manager import PROFILE_METRIC, NmIpv4, RealNetworkManager
from broxy4g.adapters.shell import FakeShell, ShellResult

IFACE = "enx02360c7c147a"
NAME = "broxy4g-phone-ABC123"

_VERIFY_FIELDS = (
    "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,"
    "connection.autoconnect,connection.interface-name"
)

_DHCI4_OUT = (
    "DHCP4.IP_ADDRESS[0]:  192.168.172.78\n"
    "DHCP4.SUBNET_MASK[0]:  255.255.255.0\n"
    "DHCP4.OPTION[27]:  routers = 192.168.172.245\n"
)


def verify_cmd() -> list[str]:
    return ["nmcli", "-g", _VERIFY_FIELDS, "con", "show", NAME]


def verify_out(metric: int = PROFILE_METRIC, interface: str = IFACE) -> str:
    return f"auto\nyes\ndisabled\n{metric}\nyes\n{interface}\n"


def gdbus_write_cmd(name: str = NAME, interface: str = IFACE, metric: int = PROFILE_METRIC) -> list[str]:
    """argv exact de l'écriture du profil par D-Bus brut (dict GVariant compris)."""
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


class RecordingShell(FakeShell):
    """FakeShell qui journalise chaque commande émise."""

    def __init__(self):
        super().__init__()
        self.calls: list[list[str]] = []

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        self.calls.append(list(command))
        return await super().run(command, timeout)


class StaleShell(RecordingShell):
    """Profil présent mais périmé (device renommé) : la 1re relecture -g échoue."""

    def __init__(self):
        super().__init__()
        self._deleted = False

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        result = await super().run(command, timeout)
        if command == verify_cmd() and not self._deleted:
            return ShellResult(0, verify_out(interface="enxold0"), "")
        if command[1:3] == ["con", "delete"] and result.returncode == 0:
            self._deleted = True
        return result


def make_shell(profile_present: bool = False) -> RecordingShell:
    shell = RecordingShell()
    shell.set_response(
        ["nmcli", "con", "show", NAME],
        ShellResult(0, f"{NAME}\n", "") if profile_present else ShellResult(1, "", "Error: connection not found"),
    )
    shell.set_response(verify_cmd(), ShellResult(0, verify_out(), ""))
    shell.set_response(gdbus_write_cmd(), ShellResult(0, "(objectpath '/org/freedesktop/NetworkManager/Settings/5',)\n", ""))
    return shell


async def test_device_ipv4_parses_address_with_prefix():
    shell = RecordingShell()
    shell.set_response(
        ["nmcli", "-g", "IP4.ADDRESS", "device", "show", IFACE],
        ShellResult(0, "192.168.172.78/24\n", ""),
    )

    assert await RealNetworkManager(shell).device_ipv4(IFACE) == NmIpv4("192.168.172.78", 24)


async def test_device_ipv4_tolerates_missing_prefix():
    shell = RecordingShell()
    shell.set_response(
        ["nmcli", "-g", "IP4.ADDRESS", "device", "show", IFACE],
        ShellResult(0, "192.168.172.78\n", ""),
    )

    assert await RealNetworkManager(shell).device_ipv4(IFACE) == NmIpv4("192.168.172.78", 0)


async def test_device_ipv4_none_when_device_missing():
    shell = RecordingShell()

    assert await RealNetworkManager(shell).device_ipv4(IFACE) is None


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

    assert await RealNetworkManager(shell).ensure_profile(IFACE, NAME) is True

    writes = [c for c in shell.calls if c[0] == "gdbus"]
    assert len(writes) == 1  # une seule écriture
    assert writes[0] == gdbus_write_cmd()  # argv exact, dict GVariant compris
    for command in shell.calls:  # plus aucun con add / con mod (cassé par le décalage de versions)
        assert command[1:3] not in (["con", "add"], ["con", "mod"])


async def test_ensure_profile_noop_when_present_and_conforming():
    shell = make_shell(profile_present=True)

    assert await RealNetworkManager(shell).ensure_profile(IFACE, NAME) is True

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

    assert await RealNetworkManager(shell).ensure_profile(IFACE, NAME) is True

    deletes = [c for c in shell.calls if c[1:3] == ["con", "delete"]]
    writes = [c for c in shell.calls if c[0] == "gdbus"]
    assert deletes == [["nmcli", "con", "delete", NAME]]
    assert writes == [gdbus_write_cmd()]
    assert shell.calls.index(deletes[0]) < shell.calls.index(writes[0])  # delete avant écriture


async def test_ensure_profile_written_settings_never_default_and_ipv6_disabled():
    shell = make_shell(profile_present=False)
    await RealNetworkManager(shell).ensure_profile(IFACE, NAME)
    variant = [c for c in shell.calls if c[0] == "gdbus"][0][-1]
    assert "'never-default': <true>" in variant
    assert f"'route-metric': <{PROFILE_METRIC}>" in variant
    assert "'ipv6': {'method': <'disabled'>}}" in variant


async def test_ensure_profile_false_when_settings_wrong():
    shell = make_shell(profile_present=True)
    shell.set_response(verify_cmd(), ShellResult(0, verify_out(interface="enxrenamed0"), ""))
    shell.set_response(["nmcli", "con", "delete", NAME], ShellResult(0, "", ""))
    shell.set_response(gdbus_write_cmd(), ShellResult(0, "(objectpath '/org/freedesktop/NetworkManager/Settings/5',)\n", ""))

    assert await RealNetworkManager(shell).ensure_profile(IFACE, NAME) is False


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
        assert await manager.ensure_profile(IFACE, NAME) is False
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
        assert await manager.ensure_profile(IFACE, NAME) is False
        assert await manager.ensure_profile(IFACE, NAME) is False
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "gdbus" in message
    assert "command not found: gdbus" in message  # extrait de stderr
    assert "libglib2.0-bin" in message  # paquet à installer dans l'image Docker
