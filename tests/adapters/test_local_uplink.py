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


class TestRealLocalUplinkLinkState:
    def _sys(self, tmp_path, name="eth0", files: dict[str, str] | None = None):
        if files:
            (tmp_path / name).mkdir(parents=True, exist_ok=True)
            for attr, value in files.items():
                (tmp_path / name / attr).write_text(value + "\n")
        return tmp_path

    async def test_operstate_up(self, tmp_path):
        sys_net = self._sys(tmp_path, files={"operstate": "up"})
        uplink = RealLocalUplink(FakeShell(), sys_class_net=sys_net)
        assert await uplink.link_state("eth0") == "up"

    async def test_operstate_lowerlayerdown_is_down(self, tmp_path):
        sys_net = self._sys(tmp_path, files={"operstate": "lowerlayerdown"})
        uplink = RealLocalUplink(FakeShell(), sys_class_net=sys_net)
        assert await uplink.link_state("eth0") == "down"

    async def test_operstate_unreadable_falls_back_to_carrier(self, tmp_path):
        sys_net = self._sys(tmp_path, files={"carrier": "1"})
        uplink = RealLocalUplink(FakeShell(), sys_class_net=sys_net)
        assert await uplink.link_state("eth0") == "up"

    async def test_carrier_zero_is_down(self, tmp_path):
        sys_net = self._sys(tmp_path, files={"carrier": "0"})
        uplink = RealLocalUplink(FakeShell(), sys_class_net=sys_net)
        assert await uplink.link_state("eth0") == "down"

    async def test_neither_file_readable_is_unknown(self, tmp_path):
        uplink = RealLocalUplink(FakeShell(), sys_class_net=tmp_path)
        assert await uplink.link_state("eth0") == "unknown"


class TestRealLocalUplinkDns:
    DNS_CMD = ["resolvectl", "dns", "eth0"]

    def _uplink(self, tmp_path, resolv_conf: str | None):
        if resolv_conf is not None:
            (tmp_path / "resolv.conf").write_text(resolv_conf)
        return RealLocalUplink(
            FakeShell(), sys_class_net=tmp_path, resolv_conf=tmp_path / "resolv.conf"
        )

    async def test_resolvectl_ips_parsed(self, tmp_path):
        shell = FakeShell()
        shell.set_response(self.DNS_CMD, ShellResult(0, "eth0: 192.168.1.1 10.0.0.53\n", ""))
        uplink = RealLocalUplink(shell, sys_class_net=tmp_path, resolv_conf=tmp_path / "r")
        assert await uplink.dns("eth0") == ["192.168.1.1", "10.0.0.53"]

    async def test_resolvctl_failure_falls_back_to_resolv_conf(self, tmp_path):
        # FakeShell par défaut : rc=1 → repli /etc/resolv.conf.
        uplink = self._uplink(tmp_path, "# comment\nnameserver 192.168.1.1\nnameserver 9.9.9.9\n")
        assert await uplink.dns("eth0") == ["192.168.1.1", "9.9.9.9"]

    async def test_resolvctl_without_iface_line_falls_back(self, tmp_path):
        (tmp_path / "resolv.conf").write_text("nameserver 1.1.1.1\n")
        shell = FakeShell()
        shell.set_response(self.DNS_CMD, ShellResult(0, "eth1: 10.0.0.1\n", ""))
        uplink = RealLocalUplink(
            shell, sys_class_net=tmp_path, resolv_conf=tmp_path / "resolv.conf"
        )
        assert await uplink.dns("eth0") == ["1.1.1.1"]

    async def test_unavailable_everywhere_is_none(self, tmp_path):
        uplink = self._uplink(tmp_path, None)
        assert await uplink.dns("eth0") is None


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
        assert await fake.link_state("eth0") == "unknown"
        assert await fake.dns("eth0") is None

    async def test_link_state_and_dns(self):
        fake = FakeLocalUplink()
        fake.set_link_state("eth0", "up")
        fake.set_dns("eth0", ["192.168.1.1"])
        fake.set_link_state("eth1", None)
        fake.set_dns("eth1", None)

        assert await fake.link_state("eth0") == "up"
        assert await fake.dns("eth0") == ["192.168.1.1"]
        assert await fake.link_state("eth1") == "unknown"
        assert await fake.dns("eth1") is None
