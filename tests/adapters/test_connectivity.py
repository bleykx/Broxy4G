"""Tests for RealConnectivity using FakeShell with real ping output."""

from __future__ import annotations

import pytest

from broxy4g.adapters.connectivity_real import RealConnectivity
from broxy4g.adapters.shell_real import FakeShell, ShellResult

# ---------------------------------------------------------------------------
# Captured real ping output from this host
# ---------------------------------------------------------------------------

REAL_PING_OK = (
    "PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.\n"
    "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=14.0 ms\n"
    "64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=13.8 ms\n"
    "64 bytes from 8.8.8.8: icmp_seq=3 ttl=117 time=14.4 ms\n"
    "64 bytes from 8.8.8.8: icmp_seq=4 ttl=117 time=14.3 ms\n"
    "\n"
    "--- 8.8.8.8 ping statistics ---\n"
    "4 packets transmitted, 4 received, 0% packet loss, time 3004ms\n"
    "rtt min/avg/max/mdev = 13.810/14.141/14.388/0.234 ms\n"
)

REAL_PING_FAIL = (
    "PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.\n"
    "--- 8.8.8.8 ping statistics ---\n"
    "4 packets transmitted, 0 received, 100% packet loss, time 3005ms\n"
)


class TestPing:
    async def test_success_parses_rtt(self):
        shell = FakeShell()
        shell.set_response(
            ["ping", "-c", "4", "8.8.8.8"],
            ShellResult(0, REAL_PING_OK, ""),
        )
        conn = RealConnectivity(shell)
        result = await conn.ping("8.8.8.8")
        assert result.host == "8.8.8.8"
        assert result.packets_sent == 4
        assert result.packets_received == 4
        assert result.min_ms == pytest.approx(13.810)
        assert result.avg_ms == pytest.approx(14.141)
        assert result.max_ms == pytest.approx(14.388)
        assert all(cmd[0] != "curl" for cmd, _ in shell.calls)

    async def test_icmp_fails_tcp_fallback(self):
        shell = FakeShell()
        shell.set_response(
            ["ping", "-c", "4", "8.8.8.8"],
            ShellResult(1, "", ""),
        )
        shell.set_response(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{time_connect}",
             "--connect-timeout", "3", "--max-time", "5", "https://8.8.8.8"],
            ShellResult(0, "0.030\n", ""),
        )
        conn = RealConnectivity(shell)
        result = await conn.ping("8.8.8.8")
        assert result.packets_received == 4
        assert result.avg_ms == pytest.approx(30.0)
        assert result.min_ms == pytest.approx(30.0)
        assert result.max_ms == pytest.approx(30.0)

    async def test_icmp_succeeds_but_no_rtt_falls_back_to_tcp(self):
        shell = FakeShell()
        # rc=0 but output without an rtt line -> avg_ms is None
        shell.set_response(
            ["ping", "-c", "4", "8.8.8.8"],
            ShellResult(0, REAL_PING_FAIL, ""),
        )
        shell.set_response(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{time_connect}",
             "--connect-timeout", "3", "--max-time", "5", "https://8.8.8.8"],
            ShellResult(0, "0.025\n", ""),
        )
        conn = RealConnectivity(shell)
        result = await conn.ping("8.8.8.8")
        assert result.packets_received == 4
        assert result.avg_ms == pytest.approx(25.0)

    async def test_tcp_fallback_with_interface(self):
        shell = FakeShell()
        shell.set_response(
            ["ping", "-c", "4", "-I", "wwan0", "8.8.8.8"],
            ShellResult(1, "", ""),
        )
        shell.set_response(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{time_connect}",
             "--connect-timeout", "3", "--max-time", "5",
             "--interface", "wwan0", "https://8.8.8.8"],
            ShellResult(0, "0.030\n", ""),
        )
        conn = RealConnectivity(shell)
        result = await conn.ping("8.8.8.8", interface="wwan0")
        curl_calls = [cmd for cmd, _ in shell.calls if cmd[0] == "curl"]
        assert len(curl_calls) == 4
        for cmd in curl_calls:
            assert "--interface" in cmd
            assert "wwan0" in cmd
        assert result.avg_ms == pytest.approx(30.0)

    async def test_both_icmp_and_tcp_fail(self):
        shell = FakeShell()
        shell.set_response(
            ["ping", "-c", "4", "8.8.8.8"],
            ShellResult(1, "", ""),
        )
        shell.set_response(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{time_connect}",
             "--connect-timeout", "3", "--max-time", "5", "https://8.8.8.8"],
            ShellResult(28, "", "timed out"),
        )
        conn = RealConnectivity(shell)
        result = await conn.ping("8.8.8.8")
        assert result.packets_received == 0
        assert result.min_ms is None
        assert result.avg_ms is None
        assert result.max_ms is None

    async def test_failure_rc_nonzero(self):
        shell = FakeShell()
        shell.set_response(
            ["ping", "-c", "4", "8.8.8.8"],
            ShellResult(1, REAL_PING_FAIL, ""),
        )
        conn = RealConnectivity(shell)
        result = await conn.ping("8.8.8.8")
        assert result.packets_received == 0
        assert result.min_ms is None
        assert result.avg_ms is None
        assert result.max_ms is None

    async def test_interface_param_in_command(self):
        shell = FakeShell()
        shell.set_response(
            ["ping", "-c", "4", "-I", "wwan0", "8.8.8.8"],
            ShellResult(0, REAL_PING_OK, ""),
        )
        conn = RealConnectivity(shell)
        await conn.ping("8.8.8.8", interface="wwan0")
        ping_cmd, _ = shell.calls[0]
        assert ping_cmd[0] == "ping"
        assert "-I" in ping_cmd
        assert "wwan0" in ping_cmd

    async def test_interface_accepts_source_ip(self):
        shell = FakeShell()
        shell.set_response(
            ["ping", "-c", "4", "-I", "10.159.25.48", "8.8.8.8"],
            ShellResult(0, REAL_PING_OK, ""),
        )
        conn = RealConnectivity(shell)
        await conn.ping("8.8.8.8", interface="10.159.25.48")
        cmd, _ = shell.calls[0]
        assert cmd[cmd.index("-I") + 1] == "10.159.25.48"

    async def test_count_param(self):
        shell = FakeShell()
        shell.set_response(
            ["ping", "-c", "2", "8.8.8.8"],
            ShellResult(0, REAL_PING_OK, ""),
        )
        conn = RealConnectivity(shell)
        await conn.ping("8.8.8.8", count=2)
        cmd, _ = shell.calls[-1]
        assert "-c" in cmd
        assert "2" in cmd


class TestGetPublicIp:
    async def test_ifconfig_first(self):
        shell = FakeShell()
        shell.set_response(
            ["curl", "-s", "--max-time", "5", "https://ifconfig.me"],
            ShellResult(0, "203.0.113.1\n", ""),
        )
        conn = RealConnectivity(shell)
        assert await conn.get_public_ip() == "203.0.113.1"

    async def test_ifconfig_with_interface(self):
        shell = FakeShell()
        shell.set_response(
            ["curl", "-s", "--max-time", "5", "--interface", "wwan0", "https://ifconfig.me"],
            ShellResult(0, "203.0.113.2\n", ""),
        )
        conn = RealConnectivity(shell)
        assert await conn.get_public_ip(interface="wwan0") == "203.0.113.2"
        cmd, _ = shell.calls[-1]
        assert "--interface" in cmd
        assert "wwan0" in cmd

    async def test_source_ip_in_command(self):
        shell = FakeShell()
        shell.set_response(
            ["curl", "-s", "--max-time", "5", "--interface", "10.159.25.48", "https://ifconfig.me"],
            ShellResult(0, "203.0.113.9\n", ""),
        )
        conn = RealConnectivity(shell)
        assert await conn.get_public_ip("10.159.25.48") == "203.0.113.9"
        cmd, _ = shell.calls[-1]
        assert cmd[cmd.index("--interface") + 1] == "10.159.25.48"

    async def test_fallback_to_ipify(self):
        shell = FakeShell()
        shell.set_response(
            ["curl", "-s", "--max-time", "5", "https://ifconfig.me"],
            ShellResult(1, "", "error"),
        )
        shell.set_response(
            ["curl", "-s", "--max-time", "5", "https://api.ipify.org"],
            ShellResult(0, "198.51.100.1\n", ""),
        )
        conn = RealConnectivity(shell)
        assert await conn.get_public_ip() == "198.51.100.1"

    async def test_ipify_with_interface(self):
        shell = FakeShell()
        shell.set_response(
            ["curl", "-s", "--max-time", "5", "--interface", "wwan0", "https://ifconfig.me"],
            ShellResult(1, "", "error"),
        )
        shell.set_response(
            ["curl", "-s", "--max-time", "5", "--interface", "wwan0", "https://api.ipify.org"],
            ShellResult(0, "198.51.100.3\n", ""),
        )
        conn = RealConnectivity(shell)
        assert await conn.get_public_ip(interface="wwan0") == "198.51.100.3"
        cmd, _ = shell.calls[-1]
        assert "--interface" in cmd

    async def test_both_fail(self):
        shell = FakeShell()
        shell.set_response(
            ["curl", "-s", "--max-time", "5", "https://ifconfig.me"],
            ShellResult(1, "", ""),
        )
        shell.set_response(
            ["curl", "-s", "--max-time", "5", "https://api.ipify.org"],
            ShellResult(1, "", ""),
        )
        conn = RealConnectivity(shell)
        assert await conn.get_public_ip() is None
