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


class TestRealInterfaceRename:
    async def test_rename_issues_down_then_name(self):
        shell = FakeShell()
        shell.set_response(
            ["ip", "link", "set", "dev", "wwan0", "down"], ShellResult(0, "", "")
        )
        shell.set_response(
            ["ip", "link", "set", "dev", "wwan0", "name", "test"], ShellResult(0, "", "")
        )
        iface = RealInterface(shell)
        await iface.rename("wwan0", "test")
        assert [cmd for cmd, _ in shell.calls] == [
            ["ip", "link", "set", "dev", "wwan0", "down"],
            ["ip", "link", "set", "dev", "wwan0", "name", "test"],
        ]

    async def test_rename_noop_when_same_name(self):
        iface = RealInterface(FakeShell())
        await iface.rename("wwan0", "wwan0")
        # aucune commande émise
        assert iface.shell.calls == []

    async def test_rename_raises_on_failure(self):
        shell = FakeShell()  # default: rc=1
        iface = RealInterface(shell)
        with pytest.raises(RuntimeError):
            await iface.rename("wwan0", "test")


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

    async def test_rename_moves_address_and_up_state(self):
        iface = FakeInterface()
        iface.set_address("wwan0", "10.0.0.1")
        await iface.set_up("wwan0")
        await iface.rename("wwan0", "test")
        assert await iface.get_ip("wwan0") is None
        assert await iface.get_ip("test") == "10.0.0.1"
        assert "test" in iface.up
        assert "wwan0" not in iface.up
        assert iface.renames == [("wwan0", "test")]

    async def test_rename_is_noop_when_same_name(self):
        iface = FakeInterface()
        iface.set_address("wwan0", "10.0.0.1")
        await iface.rename("wwan0", "wwan0")
        assert iface.renames == []
        assert await iface.get_ip("wwan0") == "10.0.0.1"
