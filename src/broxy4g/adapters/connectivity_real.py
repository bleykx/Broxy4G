import re
from dataclasses import dataclass

from .shell import ShellAdapter

TCP_PROBE_CMD = [
    "curl",
    "-s",
    "-o",
    "/dev/null",
    "-w",
    "%{time_connect}",
    "--connect-timeout",
    "3",
    "--max-time",
    "5",
]


def _empty_ping(host: str, count: int) -> "PingResult":
    return PingResult(
        host=host,
        packets_sent=count,
        packets_received=0,
        min_ms=None,
        avg_ms=None,
        max_ms=None,
    )


class RealConnectivity:
    def __init__(self, shell: ShellAdapter):
        self.shell = shell

    async def ping(
        self,
        host: str,
        count: int = 4,
        interface: str | None = None,
    ) -> "PingResult":
        result = await self._ping_icmp(host, count, interface)
        if result.packets_received == 0 or result.avg_ms is None:
            return await self._tcp_connect_latency(host, count, interface)
        return result

    async def _ping_icmp(
        self,
        host: str,
        count: int,
        interface: str | None,
    ) -> "PingResult":
        cmd = ["ping", "-c", str(count), host]
        if interface:
            cmd.insert(2, "-I")
            cmd.insert(3, interface)
        result = await self.shell.run(cmd)
        if result.returncode != 0:
            return _empty_ping(host, count)

        # Parse ping output
        lines = result.stdout.strip().split("\n")

        # Find statistics section after "---" line
        stats_line = None
        for line in lines:
            if line.startswith("---"):
                continue
            if stats_line is not None:
                break
            # Look for rtt line in the lines after "---"
            if "rtt min/avg/max" in line:
                stats_line = line
                break

        if not stats_line:
            return _empty_ping(host, count)

        # Parse statistics line like:
        # "rtt min/avg/max/mdev = 20.1/21.5/25.3/1.8 ms"
        match = re.search(
            r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms",
            stats_line,
        )
        if match:
            return PingResult(
                host=host,
                packets_sent=count,
                packets_received=count,
                min_ms=float(match.group(1)),
                avg_ms=float(match.group(2)),
                max_ms=float(match.group(3)),
            )

        return PingResult(
            host=host,
            packets_sent=count,
            packets_received=count,
            min_ms=None,
            avg_ms=None,
            max_ms=None,
        )

    async def _tcp_connect_latency(
        self,
        host: str,
        count: int,
        interface: str | None,
    ) -> "PingResult":
        cmd = list(TCP_PROBE_CMD)
        if interface is not None:
            cmd += ["--interface", interface]
        cmd.append(f"https://{host}")

        samples: list[float] = []
        for _ in range(count):
            result = await self.shell.run(cmd)
            if result.returncode != 0:
                continue
            try:
                seconds = float(result.stdout.strip())
            except ValueError:
                continue
            if seconds > 0:
                samples.append(seconds * 1000)

        if not samples:
            return _empty_ping(host, count)

        return PingResult(
            host=host,
            packets_sent=count,
            packets_received=len(samples),
            min_ms=min(samples),
            avg_ms=sum(samples) / len(samples),
            max_ms=max(samples),
        )

    async def get_public_ip(
        self,
        interface: str | None = None,
    ) -> str | None:
        if interface:
            cmd = ["curl", "-s", "--max-time", "5", "--interface", interface, "https://ifconfig.me"]
        else:
            cmd = ["curl", "-s", "--max-time", "5", "https://ifconfig.me"]
        result = await self.shell.run(cmd)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        # Fallback to ipify
        if interface:
            cmd = ["curl", "-s", "--max-time", "5", "--interface", interface, "https://api.ipify.org"]
        else:
            cmd = ["curl", "-s", "--max-time", "5", "https://api.ipify.org"]
        result = await self.shell.run(cmd)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        return None


@dataclass
class PingResult:
    host: str
    packets_sent: int
    packets_received: int
    min_ms: float | None
    avg_ms: float | None
    max_ms: float | None
