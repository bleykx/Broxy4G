from broxy4g.domain.proxy_isolation import (
    FILTER_CHAIN,
    ISOLATION_TABLE,
    MARK_CHAIN,
    NAT_CHAIN,
    ProxyUplink,
    build_isolation_ruleset,
)
from broxy4g.domain.proxy_network import DOCKER_SUBNET

# Retour d'une connexion entrante (port publié → DNAT) : doit être accepté avant
# tout marquage / drop, sinon le client LAN ne reçoit jamais la réponse.
_DNAT_ACCEPT = ("ip saddr 172.28.0.101 ct status dnat", "accept")
# Exception intra-réseau (br_netfilter) : toujours juste avant le catch-all.
_INTRA_ACCEPT = (f"ip saddr {DOCKER_SUBNET} ip daddr {DOCKER_SUBNET}", "accept")
# Deny-by-default scopé au sous-réseau proxy : toujours en dernière position.
_CATCH_ALL = (f"ip saddr {DOCKER_SUBNET}", "drop")


def _chain(ruleset, name):
    return ruleset.tables[ISOLATION_TABLE].chains[name]


def _rules(ruleset, name):
    return [(r.match, r.action) for r in _chain(ruleset, name).rules]


def test_builds_mark_nat_and_filter_chains():
    ruleset = build_isolation_ruleset(
        [ProxyUplink("172.28.0.101", 7, "wwan0")]
    )
    table = ruleset.tables[ISOLATION_TABLE]
    assert table.family == "ip"

    mark = _chain(ruleset, MARK_CHAIN)
    assert mark.type == "filter" and mark.hook == "prerouting" and mark.priority == -150
    assert _rules(ruleset, MARK_CHAIN) == [
        _DNAT_ACCEPT,
        ("ip saddr 172.28.0.101", "meta mark set 0x7"),
    ]

    nat = _chain(ruleset, NAT_CHAIN)
    assert nat.type == "nat" and nat.hook == "postrouting"
    assert _rules(ruleset, NAT_CHAIN) == [
        ('oifname "wwan0" ip saddr 172.28.0.0/16', "masquerade")
    ]

    fwd = _chain(ruleset, FILTER_CHAIN)
    assert fwd.type == "filter" and fwd.hook == "forward" and fwd.policy == "accept"
    assert _rules(ruleset, FILTER_CHAIN) == [
        _DNAT_ACCEPT,
        ('ip saddr 172.28.0.101 oifname "wwan0"', "accept"),
        _INTRA_ACCEPT,
        _CATCH_ALL,
    ]


def test_dnat_return_allowed_before_mark_and_drop():
    """Garde-fou : le retour d'une connexion entrante est accepté AVANT le
    marquage et le drop, sinon un proxy dongle publié est injoignable depuis le LAN."""
    ruleset = build_isolation_ruleset([ProxyUplink("172.28.0.101", 7, "wwan1")])

    mark = _rules(ruleset, MARK_CHAIN)
    assert mark.index(_DNAT_ACCEPT) < mark.index(
        ("ip saddr 172.28.0.101", "meta mark set 0x7")
    )

    fwd = _rules(ruleset, FILTER_CHAIN)
    assert fwd.index(_DNAT_ACCEPT) < fwd.index(
        ('ip saddr 172.28.0.101 oifname "wwan1"', "accept")
    ) < fwd.index(_INTRA_ACCEPT) < fwd.index(_CATCH_ALL)


def test_nat_rule_deduplicated_per_interface():
    ruleset = build_isolation_ruleset(
        [
            ProxyUplink("172.28.0.101", 7, "wwan0"),
            ProxyUplink("172.28.0.102", 8, "wwan0"),
        ]
    )
    assert len(_chain(ruleset, NAT_CHAIN).rules) == 1
    # Mark : 2 règles par proxy (accept retour DNAT + marquage).
    assert len(_chain(ruleset, MARK_CHAIN).rules) == 4
    # Filter : 2 règles par proxy (retour DNAT + accept oifname) + exception
    # intra-réseau + catch-all.
    assert len(_chain(ruleset, FILTER_CHAIN).rules) == 6
    assert _rules(ruleset, FILTER_CHAIN)[-1] == _CATCH_ALL


def test_offline_interface_seals_subnet():
    """Dongle hors ligne (interface inconnue) : aucune sortie proxy n'est
    autorisée, seul le retour DNAT passe, et le catch-all scelle le sous-réseau."""
    ruleset = build_isolation_ruleset([ProxyUplink("172.28.0.101", 7, None)])
    # Le proxy reste joignable depuis le LAN (retour DNAT).
    assert _rules(ruleset, MARK_CHAIN) == [
        _DNAT_ACCEPT,
        ("ip saddr 172.28.0.101", "meta mark set 0x7"),
    ]
    assert _chain(ruleset, NAT_CHAIN).rules == []
    filter_rules = _rules(ruleset, FILTER_CHAIN)
    assert not any("oifname" in match and action == "accept" for match, action in filter_rules)
    assert filter_rules == [_DNAT_ACCEPT, _INTRA_ACCEPT, _CATCH_ALL]


def test_empty_bindings_produce_scoped_deny_all():
    """Sans aucun binding : la chaîne filter contient exactement l'exception
    intra-réseau et le catch-all drop scopé au sous-réseau proxy."""
    ruleset = build_isolation_ruleset([])
    assert set(ruleset.tables) == {ISOLATION_TABLE}
    chains = ruleset.tables[ISOLATION_TABLE].chains
    assert chains[MARK_CHAIN].rules == []
    assert chains[NAT_CHAIN].rules == []
    assert _rules(ruleset, FILTER_CHAIN) == [_INTRA_ACCEPT, _CATCH_ALL]


def test_intra_subnet_exception_end_of_chain():
    """La séquence filter se termine toujours par l'exception intra-sous-réseau
    (br_netfilter : trafic inter-conteneurs du bridge) puis le catch-all,
    même sans aucun binding."""
    for bindings in (
        [ProxyUplink("172.28.0.101", 7, "wwan0")],
        [ProxyUplink("172.28.0.101", 7, None)],
        [],
    ):
        filter_rules = _rules(build_isolation_ruleset(bindings), FILTER_CHAIN)
        assert filter_rules[-2:] == [_INTRA_ACCEPT, _CATCH_ALL]
        assert filter_rules.count(_INTRA_ACCEPT) == 1
