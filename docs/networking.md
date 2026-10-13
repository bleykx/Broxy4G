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

### Nommage de l'interface dongle (jamais renommé par l'application)

**L'application ne renomme jamais l'interface réseau d'un dongle.** Un
renommage `ip link set <if> name …` fait perdre à ModemManager le port réseau
`(net)` du modem (le dongle n'expose alors que des ports AT et
`mmcli --simple-connect` dégrade en session PPP sans IPv4) ; de plus,
`ip link set name` n'étant pas persistant, tout renommage est structurellement
fragile au reboot suivant.

- Le nom d'interface est le **nom du netdev assigné par le noyau** (`wwan0`,
  `wwx0`, …), rattaché au périphérique USB du modem. Il est résolu **à la
  lecture** par l'adaptateur ModemManager (`RealModemManager` via sysfs :
  le netdev dont le chemin sysfs est sous le chemin USB du modem, avec
  frontière de séparateur stricte), puis **persisté** sur le dongle
  (`dongle.interface_name`) comme confort d'exploitation.
- Après un reset modem (ré-énumération USB), le noyau peut attribuer un autre
  nom (`wwan0` → `wwx0`, …) : le netdev est simplement **re-résolu** via
  sysfs à la reconnexion. Aucun appel `ip link set … name` n'est émis.
- **L'identité stable d'un dongle reste l'IMEI** (puis le serial USB / l'ICCID) :
  ni l'index USB ni le nom d'interface ne sont utilisés pour l'identifier.
  Le nom d'interface est un confort d'exploitation, jamais une clé.
- Conséquence côté NetworkManager : le motif `unmanaged-devices` doit couvrir
  les noms de noyau `wwan*`/`wwx*` (et pas seulement les anciens noms `dongle-*`)
  pour que NetworkManager ne se soit jamais empare de l'interface du dongle
  (cf. `docs/deployment.md`).

### Récupération douce des bearers PPP (ré-énumération USB)

Un dongle dont le bearer est dégradé en **PPP** (port `net` absent chez
ModemManager, donc aucun netdev, aucun IPv4) ne peut pas se rétablir par
reconnexion : `connect()` échoue à chaque cycle et le dongle reste `ERROR`.
La reconciliation tente alors une récupération douce —
`UsbRecoveryService` (`services/usb_recovery.py`) : écriture `0` puis `1` du
fichier sysfs `authorized` du **périphérique USB** du dongle, ce qui force
le noyau à le ré-énumérer proprement (le port `net` peut alors réapparaître
chez ModemManager au cycle suivant). Le chemin USB provient du champ
`device` rapporté par ModemManager : il est résolvable **même sans netdev**
(c'est précisément le cas PPP). L'identifiant de device visé est le
composant **le plus profond** du chemin (les dongles réels sont souvent
imbriqués derrière des hubs, ex. `…/usb1/1-1/1-1.2/1-1.2.4`) : un hub est
**jamais** ciblé. Politique : uniquement `ERROR`/`OFFLINE`,
jamais pendant un reset (verrou tenu), au plus une tentative par dongle et
par cycle, attente bornée (~10 s) de la ré-apparition du modem, no-op
journalisé si le chemin est introuvable. Détails et bornes de sécurité dans
`docs/architecture.md` ; prérequis d'écriture sysfs dans `docs/deployment.md`.

## Interface Address and Probes

Seule une IPv4 active par interface dongle. Avant toute assignation du bearer,
les adresses résiduelles sont purgées par `ip -4 addr flush dev <iface>` (opération
idempotente) ; sinon une adresse périmée resterait la 1re source et échapperait à
`ip rule from <bearer> lookup <table>`, renvoyant le trafic sur la table `main`.
Les probes (`ping`, `get_public_ip`) sont épinglés sur l'IP source du bearer
(`curl --interface <IP>`, `ping -I <IP>`), jamais sur le nom d'interface.
La table `main` et la route par défaut de l'hôte ne sont JAMAIS modifiées.
La route par défaut de la table dédiée est `default via <gw bearer> dev <iface>` ;
si le bearer n'expose **aucune passerelle**, on pose `default dev <iface>`
(scope link, sans via) avec un WARNING explicite — sinon le dongle n'aurait
aucune route par défaut du tout. La reconciliation compare la route posée au
bearer courant (`ip route show table <id>`) et répare toute dérive (ex. une
route `default via <gw LAN> dev eth0` écrasée dans la table du dongle) sans
reconnexion.

#### Plancher anti-fuite de la table dédiée (`default unreachable`)

Une table de routage dédiée ne doit **jamais** rester sans route `default` :
sinon le noyau retombe sur la dernière règle `32767 main` et le trafic marqué
du dongle suit la route par défaut de l'hôte (fuite par `eth0`). Chaque table
dédiée reçoit donc le plancher `ip route replace unreachable default table <T>`
**dès l'allocation du slot** (`services/uplink_routes.py`, réutilisée par
`ConnectionService.connect` et par la reconciliation). Au teardown, à l'échec
ou à la perte de l'état actif, la route réelle est **remplacée** par le
plancher : la table n'est jamais vide et le trafic qui essaie d'y sortir
reçoit `EHOSTUNREACH` — diagnostiquable, au lieu d'être silencieusement routé
par l'uplink de l'hôte. La `ip rule fwmark` et le slot (`route_table_id`/
`fwmark`) sont **conservés** : la table reste épinglée au dongle, et la
reconciliation repose la route réelle dès que le bearer est de retour.

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
| `proxy_filter` | `filter` / `forward` | `0` | Autorise le forward **sortant** via l'interface du dongle ; deny-by-default scopé au sous-réseau proxy (exception intra-sous-réseau) |

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
        ip saddr 172.28.0.101 oifname "wwan0" accept
        ip saddr 172.28.0.0/16 ip daddr 172.28.0.0/16 accept
        ip saddr 172.28.0.0/16 drop
    }
}
```

- La règle de `proxy_nat` est dédupliquée par interface : une seule règle de
  masquerade par uplink, quel que soit le nombre de proxies.
- La règle `accept` positive de `proxy_filter`
  (`ip saddr <ip> oifname "<iface>" accept`) n'est émise que pour un dongle
  dont l'interface est active ET connue (`ACTIVE_INTERFACE_STATES` = `ONLINE`,
  `DEGRADED`, `CONNECTING`). Le catch-all `ip saddr 172.28.0.0/16 drop` est
  émis **inconditionnellement**, quel que soit l'état du dongle : un dongle
  hors des états actifs (ou un proxy sans fwmark, une IP hors plage) n'a donc
  AUCUN `accept` de sortie ⇒ aucun réseau, aucune fuite par l'uplink de
  l'hôte (deny-by-default scopé au sous-réseau proxy).
- **Exception intra-sous-réseau** : la règle
  `ip saddr 172.28.0.0/16 ip daddr 172.28.0.0/16 accept` est émise **avant** le
  catch-all, indépendamment des bindings (elle existe même sans aucun proxy).
  Avec `br_netfilter` (actif par défaut avec Docker), le trafic IPv4 entre deux
  conteneurs du **même** bridge traverse aussi le hook `forward` : c'est du
  transit interne au bridge, pas un uplink, il ne doit donc pas être droppé.
  Toute destination **hors** sous-réseau reste refusée par le catch-all : cette
  exception n'ouvre aucune sortie vers l'hôte.
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

## Auto-réparation du bridge proxy et sonde de santé

### Auto-réparation du bridge du réseau proxy

Le réseau Docker `broxy4g-net` est créé **implicitement** au premier
`docker compose up`. À chaque cycle de réconciliation, une étape
(`ProxyNetworkService.ensure_gateway`, `services/proxy_network.py`) garantit
que le device bridge du réseau porte bien son IP passerelle
(`172.28.0.1` + préfixe `16`), de façon **idempotente** : adresse déjà
présente → aucune écriture ; réseau absent → premier démarrage → no-op
silencieux. Ne jamais `flush` ni supprimer d'adresse sur ce bridge : il
porte potentiellement des conteneurs actifs.

Mode de panne évité : sans cette IP, la table `main` perd sa route connectée
vers `172.28.0.0/16` et les paquets DNATés vers les proxys
(`172.28.0.10x:3128`) sont **droppés silencieusement** — timeouts côté
client, sans RST ni ICMP. Ré-affirmer l'adresse suffit : le noyau réinstalle
la route connectée et la passerelle redevient atteignable.

### Détection via la sonde de santé proxy

La sonde TCP du service de santé (`ProxyHealthService`,
`services/proxy_health.py`) ne teste pas un listener local de l'hôte : le
port publié est servi par DNAT, pas par un processus d'hôte. Elle cible
donc le **port publié** (`proxy.listen_port`) sur **l'adresse qu'un client
du LAN utiliserait** : l'interface d'écoute configurée, sinon la première
interface ethernet (puis wifi) portant une IPv4.
Le chemin testé depuis l'hôte est ainsi le chemin publié : chaîne OUTPUT →
DNAT → route → conteneur ; la panne du bridge ci-dessus est détectée
(`unhealthy`, raison `port publié non joignable depuis le LAN (…)`).

Limite explicite : partant de l'hôte, la sonde ne couvre pas un drop dans
la chaîne FORWARD (le parcours d'ingrès d'un paquet venant du LAN n'est pas
reproduit). Sans namespace réseau dédié, ce n'est pas mesurable depuis
l'hôte — c'est un choix assumé, à ne pas « corriger » par la sonde.

## Masquerade Docker désactivé

Le réseau `broxy4g-net` est rendu par le compose de chaque proxy
(`domain/proxy_compose.py`) avec l'option
`com.docker.network.bridge.enable_ip_masquerade: "false"` :

```yaml
networks:
  broxy4g-net:
    name: broxy4g-net
    driver: bridge
    driver_opts:
      com.docker.network.bridge.enable_ip_masquerade: "false"
    ipam:
      config:
        - subnet: 172.28.0.0/16
```

**Pourquoi** : sans cette option, Docker installe son propre masquerade
POSTROUTING sur `172.28.0.0/16` ; il SNATe le trafic fuyant du sous-réseau
proxy et le rend effectivement routable sur l'uplink de l'hôte. Notre chaîne
`proxy_nat` (masquerade conditionné à l'interface du dongle lié :
`oifname "<iface>" ip saddr 172.28.0.0/16 masquerade`) doit rester le **seul**
SNAT légitime.

**Mise à jour d'une installation existante** : Docker n'applique cette option
qu'à la **création** du réseau ; modifier le compose ne change pas un réseau
déjà existant. Procédure :

1. Arrêter les proxies (arrêter/stopper les conteneurs) ;
2. `docker network rm broxy4g-net` ;
3. Relancer / réconcilier — le réseau est recréé par le compose, avec l'option
   appliquée ;
4. Vérifier :
   `docker network inspect broxy4g-net --format '{{index .Options "com.docker.network.bridge.enable_ip_masquerade"}}'`
   (attendu : `false`), puis la passerelle `172.28.0.1/16` sur le bridge
   (`ip -4 addr show br-<id>`).

## Isolation Guarantees

1. **Network Isolation**: Each dongle's traffic is completely separated from others
2. **Proxy Isolation**: Each proxy is bound to exactly one dongle
3. **No Cross-Uplink Traffic**: Proxies cannot send traffic through other dongles
4. **Persistent Configuration**: Isolation rules persist after reboots (reconciled
   from persisted state at startup)