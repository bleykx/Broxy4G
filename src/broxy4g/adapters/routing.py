from dataclasses import dataclass
from typing import Protocol, runtime_checkable


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

    async def remove_route(self, table_id: int, dest: str) -> None:
        ...

    async def list_rules(self) -> list["RoutingRule"]:
        ...

    async def list_routes(self, table_id: int) -> list["RoutingRoute"]:
        ...


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


class FakeRouting:
    def __init__(self):
        self._rules: list[RoutingRule] = []
        self._source_rules: dict[int, str] = {}
        self._routes: list[RoutingRoute] = []

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

    async def remove_route(self, table_id: int, dest: str) -> None:
        self.remove_route_sync(table_id, dest)

    async def list_rules(self) -> list[RoutingRule]:
        return self._rules.copy()

    async def list_routes(self, table_id: int) -> list[RoutingRoute]:
        return [r for r in self._routes if r.table_id == table_id]


# Real implementation
