from pathlib import Path

import pytest

from broxy4g.adapters.nftables import (
    NftablesChain,
    NftablesRule,
    NftablesRuleset,
    NftablesTable,
)
from broxy4g.adapters.nftables_real import RealNftables
from broxy4g.adapters.shell_real import ShellResult


class _RecordingShell:
    def __init__(self, response=None):
        self.calls: list[list[str]] = []
        self._response = response or (lambda cmd: ShellResult(0, "", ""))

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        self.calls.append(command)
        return self._response(command)


def _ruleset() -> NftablesRuleset:
    table = NftablesTable(
        name="broxy4g",
        family="ip",
        chains={
            "proxy_mark": NftablesChain(
                "proxy_mark",
                [
                    NftablesRule(20, "ip saddr 172.28.0.102", "meta mark set 0x8"),
                    NftablesRule(10, "ip saddr 172.28.0.101", "meta mark set 0x7"),
                ],
                type="filter",
                hook="prerouting",
                priority=-150,
                policy="accept",
            ),
            "proxy_nat": NftablesChain(
                "proxy_nat",
                [NftablesRule(0, 'oifname "wwan0" ip saddr 172.28.0.0/16', "masquerade")],
                type="nat",
                hook="postrouting",
                priority=100,
                policy="accept",
            ),
        },
    )
    return NftablesRuleset(tables={"broxy4g": table})


def test_renderer_emits_idempotent_scoped_syntax():
    nft = RealNftables(_RecordingShell())
    syntax = nft._ruleset_to_nft_syntax(_ruleset())

    assert "table ip broxy4g {}" in syntax
    assert "flush table ip broxy4g" in syntax
    assert "type filter hook prerouting priority -150; policy accept;" in syntax
    assert "type nat hook postrouting priority 100; policy accept;" in syntax
    assert "ip saddr 172.28.0.101 meta mark set 0x7" in syntax
    assert 'oifname "wwan0" ip saddr 172.28.0.0/16 masquerade' in syntax


def test_renderer_orders_rules_by_priority():
    nft = RealNftables(_RecordingShell())
    syntax = nft._ruleset_to_nft_syntax(_ruleset())
    assert syntax.index("172.28.0.101") < syntax.index("172.28.0.102")


async def test_apply_runs_nft_without_global_flush():
    shell = _RecordingShell()
    nft = RealNftables(shell)
    await nft.apply_ruleset(_ruleset())

    assert any(cmd[:2] == ["nft", "-f"] for cmd in shell.calls)
    assert ["nft", "flush", "ruleset"] not in shell.calls


async def test_apply_writes_ruleset_inside_work_dir(tmp_path):
    shell = _RecordingShell()
    nft = RealNftables(shell, work_dir=tmp_path)
    await nft.apply_ruleset(_ruleset())

    apply_cmd = next(cmd for cmd in shell.calls if cmd[:2] == ["nft", "-f"])
    assert Path(apply_cmd[2]).parent == tmp_path


def test_work_dir_is_created_when_missing(tmp_path):
    target = tmp_path / "run"
    RealNftables(_RecordingShell(), work_dir=target)
    assert target.is_dir()


async def test_apply_empty_ruleset_is_noop():
    shell = _RecordingShell()
    nft = RealNftables(shell)
    await nft.apply_ruleset(NftablesRuleset(tables={}))
    assert shell.calls == []


async def test_apply_raises_on_failure():
    shell = _RecordingShell(lambda cmd: ShellResult(1, "", "boom"))
    nft = RealNftables(shell)
    with pytest.raises(RuntimeError):
        await nft.apply_ruleset(_ruleset())


async def test_flush_only_touches_managed_tables():
    shell = _RecordingShell()
    nft = RealNftables(shell)
    await nft.apply_ruleset(_ruleset())
    shell.calls.clear()

    await nft.flush()

    assert ["nft", "flush", "table", "ip", "broxy4g"] in shell.calls
    assert ["nft", "flush", "ruleset"] not in shell.calls


_JSON_RULESET = """
{"nftables": [
  {"metainfo": {"version": "1.0.6"}},
  {"table": {"family": "ip", "name": "broxy4g", "handle": 1}},
  {"chain": {"family": "ip", "table": "broxy4g", "name": "proxy_mark",
             "handle": 2, "type": "filter", "hook": "prerouting",
             "prio": -150, "policy": "accept"}}
]}
"""


async def test_get_ruleset_parses_json():
    def response(cmd):
        if cmd[:3] == ["nft", "-j", "list"]:
            return ShellResult(0, _JSON_RULESET, "")
        return ShellResult(0, "", "")

    nft = RealNftables(_RecordingShell(response))
    ruleset = await nft.get_ruleset()

    assert "broxy4g" in ruleset.tables
    chain = ruleset.tables["broxy4g"].chains["proxy_mark"]
    assert chain.type == "filter"
    assert chain.hook == "prerouting"
    assert chain.priority == -150
    assert chain.policy == "accept"


async def test_get_ruleset_tolerates_invalid_json():
    nft = RealNftables(_RecordingShell(lambda cmd: ShellResult(0, "not json", "")))
    assert await nft.get_ruleset() == NftablesRuleset(tables={})
