"""RealNetworkManager piloté par FakeShell avec sorties nmcli en boîtes."""

from broxy4g.adapters.network_manager import PROFILE_METRIC, NmIpv4, RealNetworkManager
from broxy4g.adapters.shell import FakeShell, ShellResult

IFACE = "enx02360c7c147a"
NAME = "broxy4g-phone-ABC123"

_DHCI4_OUT = (
    "DHCP4.IP_ADDRESS[0]:  192.168.172.78\n"
    "DHCP4.SUBNET_MASK[0]:  255.255.255.0\n"
    "DHCP4.OPTION[27]:  routers = 192.168.172.245\n"
)


class RecordingShell(FakeShell):
    """FakeShell qui journalise chaque commande émise."""

    def __init__(self):
        super().__init__()
        self.calls: list[list[str]] = []

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        self.calls.append(list(command))
        return await super().run(command, timeout)


def make_shell(profile_present: bool = False) -> RecordingShell:
    shell = RecordingShell()
    shell.set_response(
        ["nmcli", "con", "show", NAME],
        ShellResult(0, f"{NAME}\n", "") if profile_present else ShellResult(1, "", "Error: connection not found"),
    )
    shell.set_response(
        ["nmcli", "-g",
         "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,connection.autoconnect",
         "con", "show", NAME],
        ShellResult(0, f"auto\nyes\ndisabled\n{PROFILE_METRIC}\nyes\n", ""),
    )
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


async def test_ensure_profile_adds_when_absent():
    shell = make_shell(profile_present=False)

    assert await RealNetworkManager(shell).ensure_profile(IFACE, NAME) is True

    adds = [c for c in shell.calls if "add" in c and "type" in c]
    assert len(adds) == 1
    assert adds[0] == [
        "nmcli", "con", "add", "type", "ethernet", "ifname", IFACE, "con-name", NAME,
        "ipv4.method", "auto", "ipv4.never-default", "yes", "ipv6.method", "disabled",
        "ipv4.route-metric", str(PROFILE_METRIC), "connection.autoconnect", "yes",
    ]


async def test_ensure_profile_mods_when_present():
    shell = make_shell(profile_present=True)

    assert await RealNetworkManager(shell).ensure_profile(IFACE, NAME) is True

    mods = [c for c in shell.calls if c[2] == "mod"]
    assert len(mods) == 1
    assert "add" not in mods[0]


async def test_ensure_profile_settings_always_never_default_and_ipv6_disabled():
    for present in (False, True):
        shell = make_shell(profile_present=present)
        await RealNetworkManager(shell).ensure_profile(IFACE, NAME)
        for command in shell.calls:
            if "add" not in command and command[2:3] != ["mod"]:
                continue
            assert "ipv4.never-default" in command and command[command.index("ipv4.never-default") + 1] == "yes"
            assert "ipv6.method" in command and command[command.index("ipv6.method") + 1] == "disabled"


async def test_ensure_profile_false_when_settings_wrong():
    shell = make_shell(profile_present=True)
    shell.set_response(
        ["nmcli", "-g",
         "ipv4.method,ipv4.never-default,ipv6.method,ipv4.route-metric,connection.autoconnect",
         "con", "show", NAME],
        ShellResult(0, "auto\nno\ndisabled\n21000\nyes\n", ""),
    )

    assert await RealNetworkManager(shell).ensure_profile(IFACE, NAME) is False
