import asyncio

from broxy4g.adapters.nftables import NftablesRuleset
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import Proxy, ProxySource
from broxy4g.domain.proxy_isolation import ISOLATION_TABLE, build_isolation_ruleset
from broxy4g.domain.proxy_network import DOCKER_SUBNET
from broxy4g.services.network_isolation import NetworkIsolationService

# Exception intra-réseau (br_netfilter) : toujours juste avant le catch-all.
_INTRA_ACCEPT = (f"ip saddr {DOCKER_SUBNET} ip daddr {DOCKER_SUBNET}", "accept")
# Deny-by-default scopé au sous-réseau proxy : toujours en dernière position.
_CATCH_ALL = (f"ip saddr {DOCKER_SUBNET}", "drop")


def _filter_rules(ruleset):
    return [(r.match, r.action) for r in _chains(ruleset)["proxy_filter"].rules]


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

    bindings = await isolation.bindings()
    assert bindings == []
    # Proxy sans fwmark : aucune sortie autorisée, le catch-all scelle le
    # sous-réseau (l'exception intra-réseau reste présente).
    ruleset = build_isolation_ruleset(bindings)
    assert _filter_rules(ruleset) == [_INTRA_ACCEPT, _CATCH_ALL]


async def test_bindings_drop_interface_when_dongle_offline(
    isolation, dongle_repo, proxy_repo, fake_nft
):
    dongle = await _seed_dongle(dongle_repo, state=DongleState.OFFLINE)
    await _seed_proxy(proxy_repo, dongle.id)

    bindings = await isolation.bindings()
    # Le proxy reste rattaché (retour DNAT + marquage) mais sans interface.
    assert bindings[0].interface_name is None

    # Le ruleset appliqué refuse toute sortie proxy : pas de `accept` oifname,
    # le catch-all scelle le sous-réseau.
    await isolation.apply()
    ruleset = await fake_nft.get_ruleset()
    filter_rules = _filter_rules(ruleset)
    assert not any(
        "oifname" in match and action == "accept" for match, action in filter_rules
    )
    assert filter_rules[-1] == _CATCH_ALL


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
    assert _filter_rules(ruleset) == [
        ("ip saddr 172.28.0.101 ct status dnat", "accept"),
        ('ip saddr 172.28.0.101 oifname "wwan0"', "accept"),
        _INTRA_ACCEPT,
        _CATCH_ALL,
    ]


async def test_apply_without_bindings_seals_subnet(isolation, fake_nft):
    """Aucun binding : la chaîne filter contient exactement l'exception
    intra-réseau et le catch-all drop."""
    await isolation.apply()

    ruleset = await fake_nft.get_ruleset()
    chains = _chains(ruleset)
    assert chains["proxy_mark"].rules == []
    assert chains["proxy_nat"].rules == []
    assert _filter_rules(ruleset) == [_INTRA_ACCEPT, _CATCH_ALL]


async def test_no_leak_outside_dongle_interface(
    isolation, dongle_repo, proxy_repo, fake_nft
):
    """Pas de fuite : un proxy ne sort que par l'interface de son dongle, et
    le catch-all suit chaque accept positif."""
    wwan0 = await _seed_dongle(dongle_repo, imei="123456789012345")
    await _seed_proxy(proxy_repo, wwan0.id, ip="172.28.0.101", port=31201)
    wwan1 = await _seed_dongle(
        dongle_repo, imei="123456789012346", fwmark=8, interface="wwan1"
    )
    await _seed_proxy(proxy_repo, wwan1.id, ip="172.28.0.102", port=31202)

    await isolation.apply()
    ruleset = await fake_nft.get_ruleset()
    filter_rules = _filter_rules(ruleset)

    accept_wwan0 = ('ip saddr 172.28.0.101 oifname "wwan0"', "accept")
    accept_wwan1 = ('ip saddr 172.28.0.102 oifname "wwan1"', "accept")
    assert accept_wwan0 in filter_rules
    assert accept_wwan1 in filter_rules
    # Le catch-all suit (dernière position) : tout le reste du sous-réseau est
    # refusé.
    assert filter_rules[-1] == _CATCH_ALL
    assert filter_rules.index(_CATCH_ALL) > filter_rules.index(accept_wwan1)
    # Aucun accept pour une autre interface que celle du dongle du proxy.
    for match, action in filter_rules:
        if action == "accept" and "oifname" in match:
            assert match in (accept_wwan0[0], accept_wwan1[0])


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
