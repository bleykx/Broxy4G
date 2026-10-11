import asyncio

from broxy4g.adapters.nftables import NftablesRuleset
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import Proxy, ProxySource
from broxy4g.domain.proxy_isolation import ISOLATION_TABLE
from broxy4g.services.network_isolation import NetworkIsolationService


async def _seed_dongle(
    dongle_repo,
    *,
    imei="123456789012345",
    fwmark: int | None = 7,
    interface="wwan0",
    state=DongleState.ONLINE,
):
    dongle = Dongle(identity=IdentityMaterial(imei=imei), label="Test")
    dongle.fwmark = fwmark
    dongle.interface_name = interface
    dongle.state = state
    await dongle_repo.save(dongle)
    return dongle


async def _seed_proxy(
    proxy_repo, dongle_id, *, ip="172.28.0.101", port=31201, enabled=True
):
    proxy = Proxy(dongle_id=dongle_id, container_name="broxy4g-p", listen_port=port)
    proxy.network_ip = ip
    proxy.enabled = enabled
    await proxy_repo.save(proxy)
    return proxy


def _chains(ruleset):
    return ruleset.tables[ISOLATION_TABLE].chains


async def test_bindings_links_enabled_proxy_to_online_dongle(
    isolation, dongle_repo, proxy_repo
):
    dongle = await _seed_dongle(dongle_repo)
    await _seed_proxy(proxy_repo, dongle.id)

    bindings = await isolation.bindings()

    assert len(bindings) == 1
    assert bindings[0].network_ip == "172.28.0.101"
    assert bindings[0].fwmark == 7
    assert bindings[0].interface_name == "wwan0"


async def test_bindings_skip_disabled_proxy_and_missing_fwmark(
    isolation, dongle_repo, proxy_repo
):
    dongle = await _seed_dongle(dongle_repo, fwmark=None)
    await _seed_proxy(proxy_repo, dongle.id)
    other = await _seed_dongle(dongle_repo, imei="123456789012346", interface="wwan1")
    await _seed_proxy(proxy_repo, other.id, ip="172.28.0.102", port=31202, enabled=False)

    assert await isolation.bindings() == []


async def test_bindings_drop_interface_when_dongle_offline(
    isolation, dongle_repo, proxy_repo
):
    dongle = await _seed_dongle(dongle_repo, state=DongleState.OFFLINE)
    await _seed_proxy(proxy_repo, dongle.id)

    bindings = await isolation.bindings()

    assert bindings[0].interface_name is None


async def _seed_local(proxy_repo, *, ip="172.28.0.150", fwmark=9, enabled=True):
    proxy = Proxy(
        dongle_id=None,
        source_type=ProxySource.LOCAL,
        source_ref="eth0",
        interface_name="eth0",
        container_name="broxy4g-local",
        listen_port=31250,
    )
    proxy.network_ip = ip
    proxy.fwmark = fwmark
    proxy.enabled = enabled
    await proxy_repo.save(proxy)
    return proxy


async def test_bindings_include_local_proxy(isolation, proxy_repo):
    await _seed_local(proxy_repo)

    bindings = await isolation.bindings()

    assert len(bindings) == 1
    assert bindings[0].network_ip == "172.28.0.150"
    assert bindings[0].fwmark == 9
    assert bindings[0].interface_name == "eth0"


async def test_bindings_skip_disabled_local_proxy(isolation, proxy_repo):
    await _seed_local(proxy_repo, enabled=False)

    assert await isolation.bindings() == []


async def test_apply_writes_isolation_table(isolation, dongle_repo, proxy_repo, fake_nft):
    dongle = await _seed_dongle(dongle_repo)
    await _seed_proxy(proxy_repo, dongle.id)

    await isolation.apply()

    ruleset = await fake_nft.get_ruleset()
    chains = _chains(ruleset)
    assert [(r.match, r.action) for r in chains["proxy_mark"].rules] == [
        ("ip saddr 172.28.0.101 ct status dnat", "accept"),
        ("ip saddr 172.28.0.101", "meta mark set 0x7"),
    ]
    assert [(r.match, r.action) for r in chains["proxy_nat"].rules] == [
        ('oifname "wwan0" ip saddr 172.28.0.0/16', "masquerade")
    ]


async def test_apply_without_bindings_clears_rules(isolation, fake_nft):
    await isolation.apply()

    ruleset = await fake_nft.get_ruleset()
    assert all(not c.rules for c in _chains(ruleset).values())


class _ReentranceNft:
    """Faux nftables qui compte le nombre d'apply_ruleset simultanés."""

    def __init__(self):
        self.depth = 0
        self.max_depth = 0
        self._ruleset = None

    async def apply_ruleset(self, ruleset):
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)
        await asyncio.sleep(0)  # rend la main : un apply concurrent s'entremèlerait ici
        self._ruleset = ruleset
        self.depth -= 1

    async def get_ruleset(self):
        return self._ruleset or NftablesRuleset(tables={})

    async def flush(self):
        self._ruleset = None


async def test_apply_is_single_flight(dongle_repo, proxy_repo):
    """M1 : plusieurs apply() concurrents ne s'entrelacent jamais (single-flight)."""
    nft = _ReentranceNft()
    isolation = NetworkIsolationService(proxy_repo, dongle_repo, nft)
    dongle = await _seed_dongle(dongle_repo)
    await _seed_proxy(proxy_repo, dongle.id)

    await asyncio.gather(isolation.apply(), isolation.apply(), isolation.apply())

    assert nft.max_depth == 1
