from broxy4g.adapters.nftables import FakeNftables, NftablesRuleset


class TestFakeNftables:
    async def test_apply_ruleset(self):
        nftables = FakeNftables()
        ruleset = NftablesRuleset(tables={})
        await nftables.apply_ruleset(ruleset)
        result = await nftables.get_ruleset()
        assert result == ruleset

    async def test_get_ruleset_empty(self):
        nftables = FakeNftables()
        result = await nftables.get_ruleset()
        assert result == NftablesRuleset(tables={})

    async def test_flush(self):
        nftables = FakeNftables()
        ruleset = NftablesRuleset(tables={})
        await nftables.apply_ruleset(ruleset)
        await nftables.flush()
        result = await nftables.get_ruleset()
        assert result == NftablesRuleset(tables={})

    async def test_idempotence_apply_ruleset(self):
        nftables = FakeNftables()
        ruleset = NftablesRuleset(tables={})
        await nftables.apply_ruleset(ruleset)
        await nftables.apply_ruleset(ruleset)
        result = await nftables.get_ruleset()
        assert result == ruleset
