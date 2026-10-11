from broxy4g.domain.proxy_isolation import (
    FILTER_CHAIN,
    ISOLATION_TABLE,
    MARK_CHAIN,
    NAT_CHAIN,
    ProxyUplink,
    build_isolation_ruleset,
)

# Retour d'une connexion entrante (port publié → DNAT) : doit être accepté avant
# tout marquage / drop, sinon le client LAN ne reçoit jamais la réponse.
_DNAT_ACCEPT = ("ip saddr 172.28.0.101 ct status dnat", "accept")


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
    assert fwd.type == "filter" and fwd.hook == "forward"
    assert _rules(ruleset, FILTER_CHAIN) == [
        _DNAT_ACCEPT,
        ('ip saddr 172.28.0.101 oifname != "wwan0"', "drop"),
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
        ('ip saddr 172.28.0.101 oifname != "wwan1"', "drop")
    )


def test_nat_rule_deduplicated_per_interface():
    ruleset = build_isolation_ruleset(
        [
            ProxyUplink("172.28.0.101", 7, "wwan0"),
            ProxyUplink("172.28.0.102", 8, "wwan0"),
        ]
    )
    assert len(_chain(ruleset, NAT_CHAIN).rules) == 1
    # 2 règles par proxy (accept retour DNAT + marquage / drop).
    assert len(_chain(ruleset, MARK_CHAIN).rules) == 4
    assert len(_chain(ruleset, FILTER_CHAIN).rules) == 4


def test_offline_interface_keeps_mark_only():
    ruleset = build_isolation_ruleset([ProxyUplink("172.28.0.101", 7, None)])
    # Même uplink hors ligne : le proxy reste joignable depuis le LAN (retour DNAT).
    assert _rules(ruleset, MARK_CHAIN) == [
        _DNAT_ACCEPT,
        ("ip saddr 172.28.0.101", "meta mark set 0x7"),
    ]
    assert _chain(ruleset, NAT_CHAIN).rules == []
    assert _chain(ruleset, FILTER_CHAIN).rules == []


def test_empty_bindings_produce_empty_table():
    ruleset = build_isolation_ruleset([])
    assert set(ruleset.tables) == {ISOLATION_TABLE}
    assert all(not c.rules for c in ruleset.tables[ISOLATION_TABLE].chains.values())
