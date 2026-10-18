"""Tests for the interface adapter (address lifecycle helpers)."""

import pytest

from broxy4g.adapters.net_interface import FakeInterface, RealInterface
from broxy4g.adapters.shell_real import FakeShell, ShellResult


class TestRealInterfaceFlush:
    async def test_flush_ipv4_issues_expected_command(self):
        shell = FakeShell()
        shell.set_response(
            ["ip", "-4", "addr", "flush", "dev", "wwan0"],
            ShellResult(0, "", ""),
        )
        iface = RealInterface(shell)
        await iface.flush_ipv4("wwan0")
        cmd, _ = shell.calls[-1]
        assert cmd == ["ip", "-4", "addr", "flush", "dev", "wwan0"]

    async def test_flush_ipv4_raises_on_failure(self):
        shell = FakeShell()  # default mock returns rc=1
        iface = RealInterface(shell)
        with pytest.raises(RuntimeError):
            await iface.flush_ipv4("wwan0")


class TestRealInterfacePrimaryIpv4:
    """``primary_ipv4`` : la vérité noyau (adresse primaire, pas l'ordre ``nmcli``)."""

    CMD = ["ip", "-o", "-4", "addr", "show", "dev", "wwan0", "scope", "global"]

    def _iface(self, output: str, rc: int = 0) -> RealInterface:
        shell = FakeShell()
        shell.set_response(self.CMD, ShellResult(rc, output, ""))
        return RealInterface(shell)

    async def test_uses_exact_argv(self):
        shell = FakeShell()
        shell.set_response(
            self.CMD,
            ShellResult(0, "4: wwan0    inet 10.0.0.2/30 scope global wwan0\n", ""),
        )
        iface = RealInterface(shell)
        await iface.primary_ipv4("wwan0")
        cmd, _ = shell.calls[-1]
        assert cmd == self.CMD

    async def test_secondary_first_then_primary_returns_primary(self):
        """Une ``secondary`` en tête est ignorée : c'est la primaire qui compte."""
        iface = self._iface(
            "4: wwan0    inet 192.168.172.79/24 brd 192.168.172.255 scope global secondary wwan0\n"
            "4: wwan0    inet 192.168.172.78/24 brd 192.168.172.255 scope global wwan0\n"
        )
        assert await iface.primary_ipv4("wwan0") == ("192.168.172.78", 24)

    async def test_link_local_only_returns_none(self):
        iface = self._iface(
            "4: wwan0    inet 169.254.10.5/16 brd 169.254.255.255 scope link wwan0\n"
        )
        assert await iface.primary_ipv4("wwan0") is None

    async def test_empty_output_returns_none(self):
        assert await self._iface("").primary_ipv4("wwan0") is None

    async def test_nonzero_rc_returns_none(self):
        assert await self._iface("", rc=1).primary_ipv4("wwan0") is None

    async def test_corrupt_prefix_returns_none_without_exception(self):
        iface = self._iface("4: wwan0    inet 192.168.1.1/abc scope global wwan0\n")
        assert await iface.primary_ipv4("wwan0") is None

    async def test_unparseable_line_returns_none(self):
        iface = self._iface("garbage line without inet\n")
        assert await iface.primary_ipv4("wwan0") is None


class TestRealInterfaceGetIp:
    async def test_returns_first_inet(self):
        shell = FakeShell()
        shell.set_response(
            ["ip", "-o", "-4", "addr", "show", "dev", "wwan0"],
            ShellResult(
                0,
                "4: wwan0    inet 10.95.117.182/30 brd 10.95.117.183 scope global wwan0\n"
                "4: wwan0    inet 10.159.25.48/27 brd 10.159.25.63 scope global wwan0\n",
                "",
            ),
        )
        iface = RealInterface(shell)
        assert await iface.get_ip("wwan0") == "10.95.117.182"

    async def test_returns_none_on_failure(self):
        shell = FakeShell()
        iface = RealInterface(shell)
        assert await iface.get_ip("wwan0") is None


class TestFakeInterface:
    async def test_flush_removes_address(self):
        iface = FakeInterface()
        iface.set_address("wwan0", "10.0.0.1")
        await iface.flush_ipv4("wwan0")
        assert await iface.get_ip("wwan0") is None

    async def test_flush_is_idempotent(self):
        iface = FakeInterface()
        await iface.flush_ipv4("wwan0")
        assert await iface.get_ip("wwan0") is None

    async def test_set_addresses_primary_ipv4_skips_secondary(self):
        iface = FakeInterface()
        iface.set_addresses(
            "wwan0",
            [("192.168.172.79", 24, True), ("192.168.172.78", 24, False)],
        )
        assert await iface.primary_ipv4("wwan0") == ("192.168.172.78", 24)
        # ``get_ip`` conserve le contrat : première adresse dans l'ordre d'insertion.
        assert await iface.get_ip("wwan0") == "192.168.172.79"

    async def test_set_addresses_only_secondary_returns_none(self):
        iface = FakeInterface()
        iface.set_addresses("wwan0", [("192.168.172.79", 24, True)])
        assert await iface.primary_ipv4("wwan0") is None

    async def test_primary_ipv4_no_addresses_returns_none(self):
        assert await FakeInterface().primary_ipv4("wwan0") is None
