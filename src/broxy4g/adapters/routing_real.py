import re

from .routing import SOURCE_RULE_PRIORITY_OFFSET, RoutingRoute, RoutingRule
from .shell import ShellAdapter

# "default via 10.159.56.49 dev dongle-1336 onlink" (avec passerelle)
# ou "default dev wwan1 scope link" (sans passerelle, scope link).
# Les noms d'interface peuvent contenir des tirets : pas de \w+ ici.
_DEFAULT_ROUTE_RE = re.compile(r"^default(?:\s+via\s+(?P<via>\S+))?\s+dev\s+(?P<dev>[A-Za-z0-9._:@-]+)")

# Plancher de table : "unreachable default" (idem "blackhole default",
# "prohibit default") — sans dev, _DEFAULT_ROUTE_RE ne le verrait pas.
_BARRIER_ROUTE_RE = re.compile(r"^(?P<type>unreachable|blackhole|prohibit)\s+default\b")


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

    async def add_barrier_route(self, table_id: int) -> None:
        # Plancher : une table dédiée ne doit jamais être vide, sinon le noyau
        # retombe sur la route par défaut de la table main (fuite vers l'uplink hôte).
        await self._run_checked(
            "ip route replace unreachable",
            ["ip", "route", "replace", "unreachable", "default", "table", str(table_id)],
        )

    async def remove_route(self, table_id: int, dest: str) -> None:
        await self.shell.run(["ip", "route", "del", dest, "table", str(table_id)])

    async def list_rules(self) -> list[RoutingRule]:
        result = await self.shell.run(["ip", "rule", "show"])
        if result.returncode != 0:
            return []

        rules = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            # Exemples réels (l'ordre des champs varie, on ne l'anticipe pas) :
            #   "10001:  from all fwmark 0x1 lookup 10001"   (fwmark en hexa)
            #   "11001:  from 10.159.56.48 lookup 10001"     (règle source)
            match = re.match(r"^(?P<pref>\d+):\s*(?P<rest>.*\S)\s*$", line)
            if not match:
                continue
            rest = match.group("rest")
            lookup = re.search(r"\blookup\s+(\d+)", rest)
            if not lookup:
                continue
            table_id = int(lookup.group(1))
            fwmark = re.search(r"\b(?:ip)?fwmark\s+(0x)?([0-9a-fA-F]+)", rest)
            if fwmark:
                rules.append(RoutingRule(
                    table_id=table_id,
                    fwmark=int(fwmark.group(2), 16),
                    priority=int(match.group("pref")),
                ))
                continue
            source = re.search(r"\bfrom\s+(\S+)", rest)
            if source and source.group(1) != "all":
                rules.append(RoutingRule(
                    table_id=table_id,
                    fwmark=0,
                    priority=int(match.group("pref")),
                    source=source.group(1),
                ))
        return rules

    async def list_routes(self, table_id: int) -> list[RoutingRoute]:
        result = await self.shell.run(["ip", "route", "show", "table", str(table_id)])
        if result.returncode != 0:
            return []

        routes = []
        for line in result.stdout.splitlines():
            line = line.strip()
            barrier = _BARRIER_ROUTE_RE.match(line)
            if barrier:
                # Plancher de table (ex. "unreachable default") : idempotence.
                routes.append(RoutingRoute(
                    table_id=table_id,
                    dest="default",
                    via=None,
                    dev=None,
                    type=barrier.group("type"),
                ))
                continue
            match = _DEFAULT_ROUTE_RE.match(line)
            if not match:
                # Routes non-default (ex. "10.159.56.32/27 dev dongle-1336
                # proto kernel scope link src 10.159.56.48") : ignorées.
                continue
            routes.append(RoutingRoute(
                table_id=table_id,
                dest="default",
                via=match.group("via"),
                dev=match.group("dev"),
            ))
        return routes
