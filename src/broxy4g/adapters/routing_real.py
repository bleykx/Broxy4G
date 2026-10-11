import re
from dataclasses import dataclass

from .shell import ShellAdapter

# Décalage de priorité pour la règle "from <ip>" (trafic propre du dongle).
SOURCE_RULE_PRIORITY_OFFSET = 1000


class RealRouting:
    def __init__(self, shell: ShellAdapter):
        self.shell = shell

    async def _run_checked(self, action: str, command: list[str]) -> None:
        result = await self.shell.run(command)
        if result.returncode != 0:
            raise RuntimeError(
                f"{action} failed (rc={result.returncode}): {result.stderr or result.stdout}"
            )

    async def add_rule(self, table_id: int, fwmark: int) -> None:
        pref = table_id
        # Le del préalable peut légitimement échouer si la règle n'existe pas.
        await self.shell.run(["ip", "rule", "del", "pref", str(pref)])
        await self._run_checked(
            "ip rule add",
            ["ip", "rule", "add", "fwmark", str(fwmark), "table", str(table_id), "pref", str(pref)],
        )

    async def remove_rule(self, table_id: int, fwmark: int) -> None:
        await self.shell.run(["ip", "rule", "del", "fwmark", str(fwmark), "table", str(table_id)])

    async def add_source_rule(self, address: str, table_id: int) -> None:
        pref = table_id + SOURCE_RULE_PRIORITY_OFFSET
        # Le del préalable peut légitimement échouer si la règle n'existe pas.
        await self.shell.run(["ip", "rule", "del", "pref", str(pref)])
        await self._run_checked(
            "ip rule add",
            ["ip", "rule", "add", "from", address, "table", str(table_id), "pref", str(pref)],
        )

    async def remove_source_rule(self, table_id: int) -> None:
        pref = table_id + SOURCE_RULE_PRIORITY_OFFSET
        await self.shell.run(["ip", "rule", "del", "pref", str(pref)])

    async def add_route(self, table_id: int, dest: str, via: str | None, dev: str) -> None:
        cmd = ["ip", "route", "replace", dest]
        if via:
            cmd.extend(["via", via])
        cmd.extend(["dev", dev, "table", str(table_id)])
        if via and dest == "default":
            cmd.append("onlink")
        elif via is None:
            cmd.extend(["scope", "link"])
        await self._run_checked("ip route replace", cmd)

    async def remove_route(self, table_id: int, dest: str) -> None:
        await self.shell.run(["ip", "route", "del", dest, "table", str(table_id)])

    async def list_rules(self) -> list["RoutingRule"]:
        result = await self.shell.run(["ip", "rule", "show"])
        if result.returncode != 0:
            return []
            
        rules = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            # Parse lines like: "10001:  from all fwmark 0x1 lookup 10001"
            match = re.match(r'^(\d+):.*fwmark\s+0x(\w+).*lookup\s+(\d+)', line)
            if match:
                rules.append(RoutingRule(
                    table_id=int(match.group(3)),
                    fwmark=int(match.group(2), 16),
                    priority=int(match.group(1))
                ))
        return rules

    async def list_routes(self, table_id: int) -> list["RoutingRoute"]:
        result = await self.shell.run(["ip", "route", "show", "table", str(table_id)])
        if result.returncode != 0:
            return []
            
        routes = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            # Parse lines like: "default dev wwan0 proto kernel scope link src 10.0.0.2"
            match = re.match(r'^default\s+dev\s+(\w+)', line)
            if match:
                routes.append(RoutingRoute(
                    table_id=table_id,
                    dest="default",
                    via=None,
                    dev=match.group(1)
                ))
        return routes


@dataclass
class RoutingRule:
    table_id: int
    fwmark: int
    priority: int


@dataclass
class RoutingRoute:
    table_id: int
    dest: str
    via: str | None
    dev: str