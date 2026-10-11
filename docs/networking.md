# Broxy4G Networking

## Per-Dongle Routing

Each dongle has its own dedicated routing table and firewall mark to ensure complete network isolation.

### Routing Table Structure

Each dongle gets a unique routing table ID (e.g., 10001, 10002, etc.) and a corresponding firewall mark (0x1, 0x2, etc.).

### IP Rule Management

Network traffic is directed to the appropriate routing table using `ip rule` commands:
```
ip rule add fwmark 0x1 lookup 10001
```

This ensures that all traffic marked with fwmark 0x1 (from dongle 1) goes through routing table 10001.

### Interface Management

Each dongle's interface is configured with:
- A unique routing table
- A dedicated firewall mark
- Appropriate routing rules

### Nommage de l'interface dongle (dérivé du label)

À chaque connexion (`ConnectionService.connect()`, donc aussi à la
reconciliation et au reset), l'interface du dongle est **renommée** pour
reprendre un nom dérivé de son `label` (`domain/interface_naming.py`) :

- Le nom est le label normalisé (NFKD, accents retirés), en minuscules, avec
  toute suite de caractères non `[a-z0-9]` réduite à un seul `-`, les `-` de
  bord retirés, et tronqué à **15 caractères** (limite Linux `IFNAMSIZ - 1`).
  Un label vide (ou sans caractère utilisable) donne le nom `dongle`.
- Les collisions entre dongles (labels identiques ou tronqués identiques)
  sont résolues **déterministement** : les dongles sont triés par
  `identity_key` (IMEI > serial USB > ICCID) ; le premier garde le nom de
  base, les suivants reçoivent un suffixe `-<4 derniers caractères
  sanitizés de l'identity_key>` (`-0000` si l'identité ne fournit aucun
  caractère utilisable), la base étant tronquée pour tenir dans 15 caractères.
  Un compteur court déterministe complète l'unicité en dernier recours.
- Le renommage est **idempotent** : si l'interface porte déjà le nom désiré,
  aucun appel système n'est émis. Sinon, l'adaptateur (`adapters/net_interface.py`)
  émet `ip link set dev <old> down` puis `ip link set dev <old> name <new>` ;
  l'interface reste down, le flux de connexion la remonte ensuite. Un échec
  de renommage échoue la connexion avec un message métier propre
  (`échec renommage interface <old> -> <new>: <detail>`).
- **L'identité stable d'un dongle reste l'IMEI / le serial USB** : ni l'index
  USB ni le nom d'interface (y compris le nom dérivé du label) ne sont
  utilisés pour l'identifier. Le nom d'interface est un confort d'exploitation,
  jamais une clé.
- Modifier le label d'un dongle change le nom d'interface **à la prochaine
  connexion** (reconciliation, reset ou reconnexion).

## Interface Address and Probes

Seule une IPv4 active par interface dongle. Avant toute assignation du bearer,
les adresses résiduelles sont purgées par `ip -4 addr flush dev <iface>` (opération
idempotente) ; sinon une adresse périmée resterait la 1re source et échapperait à
`ip rule from <bearer> lookup <table>`, renvoyant le trafic sur la table `main`.
Les probes (`ping`, `get_public_ip`) sont épinglés sur l'IP source du bearer
(`curl --interface <IP>`, `ping -I <IP>`), jamais sur le nom d'interface.
La table `main` et la route par défaut de l'hôte ne sont JAMAIS modifiées.

## nftables Isolation

Implémenté par `adapters/nftables_real.py` (`RealNftables`), piloté par
`domain/proxy_isolation.py` (`build_isolation_ruleset`) via
`services/network_isolation.py` (`NetworkIsolationService`).

Chaque proxy tourne dans le réseau Docker partagé `broxy4g-net`
(`172.28.0.0/16`) avec une IP statique unique (`172.28.0.101-199`, cf.
`domain/proxy_network.py`). L'isolation repose sur cette IP source : chaque proxy
n'est autorisé à sortir que par l'interface de son dongle.

### Table et chaînes

Toutes les règles vivent dans une seule table `ip broxy4g` (constante
`ISOLATION_TABLE`), composée de trois chaînes dédiées :

| Chaîne | Type / hook | Priorité | Rôle |
| --- | --- | --- | --- |
| `proxy_mark` | `filter` / `prerouting` | `-150` | Marque le trafic **sortant** du proxy selon son IP source ; laisse passer le retour des connexions entrantes (DNAT) |
| `proxy_nat` | `nat` / `postrouting` | `100` | Mascare le trafic proxy sur l'interface du dongle |
| `proxy_filter` | `filter` / `forward` | `0` | N'autorise le forward **sortant** que via l'interface du dongle |

### Règles générées

Pour chaque proxy actif (`enabled=True`, `dongle_id` et `network_ip` définis)
dont le dongle expose un `fwmark` :

```
table ip broxy4g {
    chain proxy_mark {
        type filter hook prerouting priority -150; policy accept
        ip saddr 172.28.0.101 ct status dnat accept
        ip saddr 172.28.0.101 meta mark set 0x7
    }
    chain proxy_nat {
        type nat hook postrouting priority 100; policy accept
        oifname "wwan0" ip saddr 172.28.0.0/16 masquerade
    }
    chain proxy_filter {
        type filter hook forward priority 0; policy accept
        ip saddr 172.28.0.101 ct status dnat accept
        ip saddr 172.28.0.101 oifname != "wwan0" drop
    }
}
```

- La règle de `proxy_nat` est dédupliquée par interface : une seule règle de
  masquerade par uplink, quel que soit le nombre de proxies.
- La règle de `proxy_filter` se limite aux états dongle où l'interface est
  considérée active (`NetworkIsolationService.ACTIVE_INTERFACE_STATES` =
  `ONLINE`, `DEGRADED`, `CONNECTING`). Un dongle hors de ces états ne produit
  qu'une règle de marquage, jamais de forward autorisé.
- **Retour des connexions entrantes (DNAT).** Quand un client du LAN joint le
  port publié d'un proxy, le conteneur répond avec sa propre IP source. Ce
  paquet de retour fait partie d'une connexion DNATée (`ct status dnat`) : la
  règle `… ct status dnat accept`, placée **avant** le marquage et le drop,
  l'exempte. Sans elle, ce retour serait marqué vers la table du dongle (et donc
  routé vers `wwan` au lieu de l'interface LAN) ou droppé par `proxy_filter`, et
  le client ne recevrait jamais la réponse — un proxy dongle publié serait
  injoignable depuis le LAN. Le trafic *initié* par le conteneur n'a pas le bit
  `ct status dnat` : il reste marqué et épinglé à l'uplink de son dongle.

### Application idempotente

`RealNftables.apply_ruleset` rend le ruleset complet puis l'applique avec
`nft -f` sur un fichier temporaire. Le pattern est : re-déclaration de
`table ip broxy4g {}` (crée la table si absente, sinon no-op), puis
`flush table ip broxy4g`, puis re-déclaration de toutes les chaînes et règles.
La table étant entièrement reconstruite à chaque appel, l'opération est
idempotente.

`RealNftables.flush` est scopé aux tables gérées (celles déjà appliquées via
`apply_ruleset`, suivies dans `self._managed`). L'hôte n'exécute jamais de
`nft flush ruleset` global ; la table `main` et la route par défaut de l'hôte
ne sont jamais touchées.

### Déclencheurs de ré-application

L'isolation est re-appliquée après toute mutation d'état : création, démarrage,
arrêt, redémarrage, reprovisionnement, rotation de credentials et suppression
d'un proxy (`services/proxy_lifecycle.py`), après connexion/déconnexion d'un
dongle (`services/connection.py`) et à la fin de chaque cycle de reconciliation
(`workers/reconciliation.py`).

## Local Interface Uplinks

Une interface réseau de l'hôte (ethernet / wifi) peut aussi être exposée comme
uplink proxy, en plus des dongles. Elle est traitée par les mêmes garanties
d'isolation :

- Elle reçoit une table de routage dédiée et un `fwmark` propres, alloués par
  `domain/routing_slots.py` (`BASE_ROUTE_TABLE_ID = 10000`, même mécanisme
  d'allocation que les dongles).
- Le proxy `ProxySource.LOCAL` correspondant est inclus dans les bindings
  nftables (`services/network_isolation.py`) : il ne peut sortir que par
  l'interface à laquelle il est épinglé, jamais par un autre uplink.
- La route par défaut de l'hôte (table `main`) n'est **jamais** modifiée ; le
  pinning se fait uniquement via `ip rule` (`fwmark` → table dédiée).
- Le pinning est reconstruit depuis la base à chaque cycle de réconciliation
  (`services/local_connections.py::restore`). Si l'interface est absente
  (wifi coupé, matériel retiré), le proxy est conservé et la réconciliation se
  contente d'un avertissement — elle ne supprime rien.
- **Pas de reset d'IP** : contrairement à un dongle, une interface hôte n'a pas
  de modem à ré-énumérer ; aucun reset de l'IP publique n'est proposé.

## Proxy Binding Rules

Each 3proxy container is explicitly linked to a specific dongle and cannot send traffic through any other uplink.

### Binding Mechanism

1. Each proxy container is created with a static IP inside `broxy4g-net`, and its
   `dongle_id` links it to exactly one dongle.
2. nftables rules (`proxy_mark`, `proxy_nat`, `proxy_filter`) constrain that
   container's traffic to the designated dongle's uplink.
3. Le binding est reconstruit depuis la base à chaque ré-application : la source
   de vérité est le couple (`proxy`, `dongle`) persisté, jamais un état volatile.

#### Listen Interface (ingress)

The binding above governs **egress** (which uplink a proxy's outgoing traffic may
use, pinned per proxy). Separately, **ingress** controls where a proxy's port is
published on the host: the *listen interface*. These are independent concerns —
the listen interface only decides which host IPv4 the published port is bound to;
it never changes the egress pinning.

- The setting is persisted as `proxy.listen_interface` (an interface name). Empty
  or unset means the default `0.0.0.0` (the port is published on all interfaces).
- Only `ethernet` and `wifi` interfaces may be chosen — never a dongle/WWAN
  interface, nor `lo`/`docker`/`veth` (`domain/proxy_listen.py`).
- The interface name is resolved to its IPv4 on every materialization and on each
  reconciliation cycle. When the chosen interface is absent or has no IPv4, the
  `ports:` block of the Docker compose is simply **omitted**: the container stays
  active but its port is not published. When the interface returns, the
  reconciliation re-publishes automatically (`ProxyProvisioningService.ensure_publish`)
  without restarting a container whose compose has not changed.
- This is a graceful contract: an absent or addressless listen interface is never
  an error that stops a proxy or crashes reconciliation — it only withholds the
  publication until the interface is back.

## Isolation Guarantees

1. **Network Isolation**: Each dongle's traffic is completely separated from others
2. **Proxy Isolation**: Each proxy is bound to exactly one dongle
3. **No Cross-Uplink Traffic**: Proxies cannot send traffic through other dongles
4. **Persistent Configuration**: Isolation rules persist after reboots (reconciled
   from persisted state at startup)