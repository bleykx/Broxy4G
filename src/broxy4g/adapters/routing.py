from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Décalage de priorité pour la règle "from <ip>" (trafic propre au dongle).
SOURCE_RULE_PRIORITY_OFFSET = 1000


@runtime_checkable
class RoutingAdapter(Protocol):
    async def add_rule(self, table_id: int, fwmark: int) -> None:
        ...

    async def remove_rule(self, table_id: int, fwmark: int) -> None:
        ...

    async def add_source_rule(self, address: str, table_id: int) -> None:
        ...

    async def remove_source_rule(self, table_id: int) -> None:
        ...

    async def add_route(self, table_id: int, dest: str, via: str | None, dev: str) -> None:
        ...

    async def add_barrier_route(self, table_id: int) -> None:
        ...

    async def remove_route(self, table_id: int, dest: str) -> None:
        ...

    async def list_rules(self) -> list["RoutingRule"]:
        ...

    async def list_routes(self, table_id: int) -> list["RoutingRoute"]:
        ...

    async def list_default_routes(self) -> list["RoutingRoute"]:
        ...

    async def remove_default_route(self, dev: str) -> bool:
        ...


@dataclass
class RoutingRule:
    table_id: int
    fwmark: int
    priority: int
    # Adresse source si la règle est une règle "from <ip>" (sinon None).
    source: str | None = None


@dataclass
class RoutingRoute:
    table_id: int
    dest: str
    via: str | None
    dev: str | None = None
    # "unicast" ou "unreachable" (plancher anti-fuite vers la table main).
    type: str = "unicast"
    # Attributs écrits par un acteur étranger (ex. NetworkManager) :
    # "default via … dev … proto dhcp src 192.168.172.79 metric 2023".
    # Notre writer ne pose JAMAIS de ``proto`` ni de ``src`` : leur présence
    # signale une route étrangère (frontière d'isolation).
    src: str | None = None
    proto: str | None = None


class FakeRouting:
    def __init__(self):
        self._rules: list[RoutingRule] = []
        self._source_rules: dict[int, str] = {}
        self._routes: list[RoutingRoute] = []
        # Routes par défaut de la TABLE MAIN (aucun filtre de table).
        self._default_routes: list[RoutingRoute] = []

    async def add_rule(self, table_id: int, fwmark: int) -> None:
        # Vérifier si la règle existe déjà
        existing = any(r for r in self._rules if r.table_id == table_id and r.fwmark == fwmark)
        if not existing:
            self._rules.append(RoutingRule(table_id, fwmark, len(self._rules) + 1))

    async def remove_rule(self, table_id: int, fwmark: int) -> None:
        self._rules = [r for r in self._rules if not (r.table_id == table_id and r.fwmark == fwmark)]

    async def add_source_rule(self, address: str, table_id: int) -> None:
        self._source_rules[table_id] = address

    async def remove_source_rule(self, table_id: int) -> None:
        self._source_rules.pop(table_id, None)

    async def add_route(self, table_id: int, dest: str, via: str | None, dev: str) -> None:
        self.remove_route_sync(table_id, dest)
        self._routes.append(RoutingRoute(table_id, dest, via, dev))

    def remove_route_sync(self, table_id: int, dest: str) -> None:
        self._routes = [r for r in self._routes if not (r.table_id == table_id and r.dest == dest)]

    async def add_barrier_route(self, table_id: int) -> None:
        # Retire toute route default de la table puis pose le plancher.
        self.remove_route_sync(table_id, "default")
        self._routes.append(RoutingRoute(table_id, "default", None, None, type="unreachable"))

    async def remove_route(self, table_id: int, dest: str) -> None:
        self.remove_route_sync(table_id, dest)

    async def list_rules(self) -> list[RoutingRule]:
        rules = self._rules.copy()
        for table_id, address in self._source_rules.items():
            rules.append(
                RoutingRule(table_id, 0, table_id + SOURCE_RULE_PRIORITY_OFFSET, source=address)
            )
        return rules

    async def list_routes(self, table_id: int) -> list[RoutingRoute]:
        return [r for r in self._routes if r.table_id == table_id]

    async def list_default_routes(self) -> list[RoutingRoute]:
        return list(self._default_routes)

    async def remove_default_route(self, dev: str) -> bool:
        before = len(self._default_routes)
        self._default_routes = [r for r in self._default_routes if r.dev != dev]
        return len(self._default_routes) < before


# Real implementation
