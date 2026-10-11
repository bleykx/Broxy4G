"""Construction pure du ruleset nftables d'isolation réseau par proxy.

Chaque proxy activé voit son trafic **sortant** (initié par le conteneur) marqué
(fwmark du dongle associé) pour être dirigé vers la table de routage dédiée du
dongle, masqué vers l'interface de ce dongle, et bloqué s'il tente de sortir par
une autre interface. Aucune règle ne touche la route par défaut de la table
`main`.

Cas particulier des connexions ENTRANTES : quand un client du LAN ouvre une
connexion vers le port publié d'un proxy, le conteneur répond avec sa propre IP
source ; ce paquet de retour appartient à une connexion DNATée (`ct status
dnat`). Il ne doit être ni marqué ni droppé, sinon la réponse du conteneur serait
routée vers l'uplink du dongle (au lieu de l'interface LAN d'où la requête est
venue) et le client ne verrait jamais la réponse. Le trafic *initié* par le
conteneur, lui, n'a pas le bit `ct status dnat` : il reste marqué et épinglé à
l'uplink de son dongle.
"""

from dataclasses import dataclass

from broxy4g.adapters.nftables import (
    NftablesChain,
    NftablesRule,
    NftablesRuleset,
    NftablesTable,
)
from broxy4g.domain.proxy_network import DOCKER_SUBNET

ISOLATION_TABLE = "broxy4g"
MARK_CHAIN = "proxy_mark"
NAT_CHAIN = "proxy_nat"
FILTER_CHAIN = "proxy_filter"

# Priorités des hooks nftables (convention nft : mangle < filter < srcnat).
PREROUTING_PRIORITY = -150
FORWARD_PRIORITY = 0
POSTROUTING_PRIORITY = 100


@dataclass(frozen=True)
class ProxyUplink:
    """Trafic d'un proxy à rattacher à l'uplink d'un dongle précis."""

    network_ip: str
    fwmark: int
    interface_name: str | None = None


def _reverse_rule(binding: ProxyUplink) -> NftablesRule:
    """Laisse passer le retour d'une connexion entrante (port publié DNATé)."""
    return NftablesRule(0, f"ip saddr {binding.network_ip} ct status dnat", "accept")


def _mark_rules(bindings: list[ProxyUplink]) -> list[NftablesRule]:
    rules: list[NftablesRule] = []
    for b in bindings:
        # Le retour d'une connexion entrante n'est jamais marqué (sinon il partirait
        # par l'uplink du dongle au lieu de revenir au client LAN).
        rules.append(_reverse_rule(b))
        rules.append(NftablesRule(0, f"ip saddr {b.network_ip}", f"meta mark set 0x{b.fwmark:x}"))
    return rules


def _nat_rules(bindings: list[ProxyUplink]) -> list[NftablesRule]:
    rules: list[NftablesRule] = []
    seen: set[str] = set()
    for b in bindings:
        if not b.interface_name or b.interface_name in seen:
            continue
        seen.add(b.interface_name)
        rules.append(
            NftablesRule(
                0,
                f'oifname "{b.interface_name}" ip saddr {DOCKER_SUBNET}',
                "masquerade",
            )
        )
    return rules


def _filter_rules(bindings: list[ProxyUplink]) -> list[NftablesRule]:
    rules: list[NftablesRule] = []
    for b in bindings:
        if not b.interface_name:
            continue
        # Autoriser explicitement le retour des connexions entrantes avant le drop :
        # le conteneur doit pouvoir répondre à un client qui a joint son port publié,
        # mais ne doit jamais initier de trafic vers une autre interface.
        rules.append(_reverse_rule(b))
        rules.append(
            NftablesRule(
                0,
                f'ip saddr {b.network_ip} oifname != "{b.interface_name}"',
                "drop",
            )
        )
    return rules


def build_isolation_ruleset(bindings: list[ProxyUplink]) -> NftablesRuleset:
    """Ruleset désiré complet de la table `ip broxy4g` (application idempotente)."""
    chains = {
        MARK_CHAIN: NftablesChain(
            MARK_CHAIN,
            _mark_rules(bindings),
            type="filter",
            hook="prerouting",
            priority=PREROUTING_PRIORITY,
            policy="accept",
        ),
        NAT_CHAIN: NftablesChain(
            NAT_CHAIN,
            _nat_rules(bindings),
            type="nat",
            hook="postrouting",
            priority=POSTROUTING_PRIORITY,
            policy="accept",
        ),
        FILTER_CHAIN: NftablesChain(
            FILTER_CHAIN,
            _filter_rules(bindings),
            type="filter",
            hook="forward",
            priority=FORWARD_PRIORITY,
            policy="accept",
        ),
    }
    table = NftablesTable(name=ISOLATION_TABLE, family="ip", chains=chains)
    return NftablesRuleset(tables={ISOLATION_TABLE: table})
