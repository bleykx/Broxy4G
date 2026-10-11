"""Tests de l'adaptateur uplink local (Real via FakeShell + sysfs en tmp_path)."""

from broxy4g.adapters.local_uplink import FakeLocalUplink, LocalInterface, RealLocalUplink
from broxy4g.adapters.shell_real import FakeShell, ShellResult

ADDR_CMD = ["ip", "-o", "-4", "addr", "show"]
GW_CMD = ["ip", "-o", "-4", "route", "show", "default", "dev", "eth0"]

ADDR_OUTPUT = (
    "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000\n"
    "    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00\n"
    "1: lo    inet 127.0.0.1/8 scope host lo\n"
    "2: eth0    inet 192.168.1.50/24 brd 192.168.1.255 scope global eth0\n"
    "3: wlp3s0    inet 10.0.0.7/24 brd 10.0.0.255 scope global wlp3s0\n"
    "4: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
    "5: veth42a1    inet 172.18.0.1/16 brd 172.18.255.255 scope global veth42a1\n"
    "6: br-abc    inet 172.19.0.1/16 brd 172.19.255.255 scope global br-abc\n"
)


def _sys_class_net(tmp_path, wifi: set[str], ethernet: set[str]):
    for name in wifi:
        (tmp_path / name / "wireless").mkdir(parents=True)
    for name in ethernet:
        (tmp_path / name / "device").mkdir(parents=True)
    return tmp_path


class TestRealLocalUplinkListInterfaces:
    async def test_parses_filters_and_sorts(self, tmp_path):
        shell = FakeShell()
        shell.set_response(ADDR_CMD, ShellResult(0, ADDR_OUTPUT, ""))
        uplink = RealLocalUplink(shell, sys_class_net=_sys_class_net(tmp_path, {"wlp3s0"}, {"eth0"}))

        interfaces = await uplink.list_interfaces()

        # lo/docker0/veth*/br-* exclus, tri par nom
        assert [i.name for i in interfaces] == ["eth0", "wlp3s0"]
        assert interfaces[0] == LocalInterface("eth0", "192.168.1.50", "ethernet")
        assert interfaces[1] == LocalInterface("wlp3s0", "10.0.0.7", "wifi")

    async def test_kind_other_without_sysfs_entry(self, tmp_path):
        shell = FakeShell()
        shell.set_response(
            ADDR_CMD,
            ShellResult(0, "2: tun0    inet 10.8.0.2/32 scope global tun0\n", ""),
        )
        uplink = RealLocalUplink(shell, sys_class_net=tmp_path)
        interfaces = await uplink.list_interfaces()
        assert interfaces == [LocalInterface("tun0", "10.8.0.2", "other")]

    async def test_first_address_wins_for_multiple_inet(self, tmp_path):
        shell = FakeShell()
        shell.set_response(
            ADDR_CMD,
            ShellResult(
                0,
                "2: eth0    inet 192.168.1.50/24 brd 192.168.1.255 scope global eth0\n"
                "2: eth0    inet 10.10.0.5/24 brd 10.10.0.255 scope global secondary eth0\n",
                "",
            ),
        )
        uplink = RealLocalUplink(shell, sys_class_net=tmp_path)
        interfaces = await uplink.list_interfaces()
        assert len(interfaces) == 1
        assert interfaces[0].ip == "192.168.1.50"

    async def test_empty_on_error(self, tmp_path):
        shell = FakeShell()  # par défaut : rc=1
        uplink = RealLocalUplink(shell, sys_class_net=tmp_path)
        assert await uplink.list_interfaces() == []

    async def test_wwan_interface_excluded_via_uevent(self, tmp_path):
        # wwan0 a un uevent DEVTYPE=wwan (dongle), eth0 est une vraie carte (device/ sans uevent wwan)
        sys_net = _sys_class_net(tmp_path, set(), {"eth0", "wwan0"})
        (sys_net / "wwan0" / "uevent").write_text(
            "DRIVER=huawei_cdc_ncm\nDEVTYPE=wwan\nINTERFACE=wwan0\n"
        )
        shell = FakeShell()
        shell.set_response(
            ADDR_CMD,
            ShellResult(
                0,
                "2: eth0    inet 192.168.1.50/24 brd 192.168.1.255 scope global eth0\n"
                "3: wwan0    inet 10.61.180.52/32 scope global wwan0\n",
                "",
            ),
        )
        uplink = RealLocalUplink(shell, sys_class_net=sys_net)

        interfaces = await uplink.list_interfaces()

        assert [i.name for i in interfaces] == ["eth0"]
        assert interfaces[0] == LocalInterface("eth0", "192.168.1.50", "ethernet")


class TestRealLocalUplinkDefaultGateway:
    async def test_present(self, tmp_path):
        shell = FakeShell()
        shell.set_response(GW_CMD, ShellResult(0, "default via 192.168.1.1 dev eth0 \n", ""))
        uplink = RealLocalUplink(shell, sys_class_net=tmp_path)
        assert await uplink.default_gateway("eth0") == "192.168.1.1"

    async def test_absent(self, tmp_path):
        shell = FakeShell()
        shell.set_response(GW_CMD, ShellResult(0, "", ""))
        uplink = RealLocalUplink(shell, sys_class_net=tmp_path)
        assert await uplink.default_gateway("eth0") is None

    async def test_rc_nonzero(self, tmp_path):
        shell = FakeShell()  # par défaut : rc=1
        uplink = RealLocalUplink(shell, sys_class_net=tmp_path)
        assert await uplink.default_gateway("eth0") is None


class TestFakeLocalUplink:
    async def test_interfaces_and_gateways(self):
        fake = FakeLocalUplink()
        fake.set_interfaces([LocalInterface("eth0", "192.168.1.50", "ethernet")])
        fake.set_gateway("eth0", "192.168.1.1")

        assert await fake.list_interfaces() == [LocalInterface("eth0", "192.168.1.50", "ethernet")]
        assert await fake.default_gateway("eth0") == "192.168.1.1"
        assert await fake.default_gateway("wlan0") is None

    async def test_set_gateway_none_clears(self):
        fake = FakeLocalUplink()
        fake.set_gateway("eth0", "192.168.1.1")
        fake.set_gateway("eth0", None)
        assert await fake.default_gateway("eth0") is None

    async def test_empty_by_default(self):
        fake = FakeLocalUplink()
        assert await fake.list_interfaces() == []
        assert await fake.default_gateway("eth0") is None
