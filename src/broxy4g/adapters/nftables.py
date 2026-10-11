from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class NftablesAdapter(Protocol):
    async def apply_ruleset(self, ruleset: "NftablesRuleset") -> None:
        ...

    async def get_ruleset(self) -> "NftablesRuleset":
        ...

    async def flush(self) -> None:
        ...


@dataclass
class NftablesRuleset:
    tables: dict[str, "NftablesTable"]


@dataclass
class NftablesTable:
    name: str
    chains: dict[str, "NftablesChain"]
    family: str = "ip"


@dataclass
class NftablesChain:
    name: str
    rules: list["NftablesRule"] = field(default_factory=list)
    type: str | None = None
    hook: str | None = None
    priority: int = 0
    policy: str | None = None


@dataclass
class NftablesRule:
    priority: int
    match: str
    action: str


class FakeNftables:
    def __init__(self):
        self._ruleset: NftablesRuleset | None = None

    async def apply_ruleset(self, ruleset: NftablesRuleset) -> None:
        self._ruleset = ruleset

    async def get_ruleset(self) -> NftablesRuleset:
        if self._ruleset is None:
            return NftablesRuleset(tables={})
        return self._ruleset

    async def flush(self) -> None:
        self._ruleset = None


# Real implementation
