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
  `wwx0`, …) ou, si un fichier systemd `.link` l'impose, un **nom stable par
  port USB physique** (`dongle-N`, cf. sous-section ci-dessous). Dans tous les
  cas il est rattaché au périphérique USB du modem et résolu **à la lecture**
  par l'adaptateur ModemManager (`RealModemManager` via sysfs : le netdev dont
  le chemin sysfs est sous le chemin USB du modem, avec frontière de
  séparateur stricte), puis **persisté** sur le dongle
  (`dongle.interface_name`) comme confort d'exploitation.
- Après un reset modem (ré-énumération USB), le noyau peut attribuer un autre
  nom (`wwan0` → `wwx0`, …) : le netdev est simplement **re-résolu** via
  sysfs à la reconnexion. Aucun appel `ip link set … name` n'est émis.
- **L'identité stable d'un dongle reste l'IMEI** (puis le serial USB / l'ICCID) :
  ni l'index USB ni le nom d'interface ne sont utilisés pour l'identifier.
  Le nom d'interface est un confort d'exploitation, jamais une clé.
- Conséquence côté NetworkManager : le motif `unmanaged-devices` doit couvrir
  les noms de noyau `wwan*`/`wwx*` **et** les noms stables `dongle-*` (cf.
  sous-section ci-dessous et `docs/deployment.md`) pour que NetworkManager ne
  s'empare jamais de l'interface du dongle.

### Nommage stable par port USB physique (fichiers systemd `.link`)

Les dongles Huawei E3372 partagent **le même MAC**
(`00:1e:10:1f:00:00`). Avec plusieurs dongles, le nom prédictible
`wwx<MAC>` est donc ambigu, et les noms `wwanN`/`wwx…` attribués par le noyau
se réattribuent à chaque ré-énumération USB. Sur les hôtes multi-dongles on
pose donc un nom **stable par port USB physique** via des fichiers
`/etc/systemd/network/10-broxy4g-dongle-N.link`, appliqués par
`systemd-udevd` **à l'énumération** du netdev :

```
# broxy4g: stable netdev name for Huawei E3372 dongle-N
# Physical USB port: <port>   (IMEI <imei>, ex <ancien nom>)
[Match]
Path=<chemin udev du port USB>

[Link]
Name=dongle-N
```

- `Path=` est le **chemin udev** (pas le chemin sysfs) : le préfixe `usb-`
  suit la forme `usb-0:<portpath>:1.1` (ex. `usb-0:1.2.2:1.1`), sans le
  préfixe `1-` de la notation sysfs. On le lit via
  `udevadm info /sys/class/net/<iface>` (propriété `ID_PATH`) ; le chemin
  sysfs complet du port reste documenté en commentaire du fichier.
- Chaque `.link` doit matcher **exactement un** netdev : vérifier avec
  `udevadm test /sys/class/net/<iface>` (« Config file … is applied »,
  « renamed from … to 'dongle-N' »).
- Le nom **doit** matcher le motif NM `dongle-*` (tiret compris) : le nom
  `dongle1` (sans tiret) serait capturé par NetworkManager.
- Un `ip link set down/up` **ne renomme pas** : l'application d'un `.link`
  nouveau ou modifié nécessite une ré-énumération du dongle (ou un reboot) —
  procédure dans `docs/deployment.md`.
- ⚠️ Observation (2026-10-15, Debian, udev 252) : sur la machine cible,
  `udevadm test /sys/class/net/<iface>` sur un netdev existant a déclenché le
  renommage **immédiatement** (renommé « while UP », confirmé dmesg),
  contrairement à l'avertissement « debug only » du manuel. Compter sur la
  ré-énumération pour appliquer un `.link`, pas sur `udevadm test`.
- Mapping en production sur proxpi (déterministe : ordre lexicographique des
  ports USB) :

  | netdev | port USB | IMEI |
  | --- | --- | --- |
  | `dongle-1` | `1-1.2.2` | `867028041329312` |
  | `dongle-2` | `1-1.2.4.1` | `867028041332043` |
  | `dongle-3` | `1-1.2.4.3` | `867028041311195` |
  | `dongle-4` | `1-1.2.4.4.2` | `867028041311336` |

- **Rollback** : supprimer les `.link` puis ré-énumérer chaque dongle →
  retour aux noms de noyau `wwan*`/`wwx*`. Non fatal : broxy4g re-résout le
  netdev par sysfs à chaque connexion, le pire cas est le statu quo.

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
par cycle, **plus un cooldown inter-cycles** (par défaut 300 s,
`BROXY4G_USB_RECOVERY_COOLDOWN_SECONDS`, en mémoire seulement) pour ne pas
ré-énumérer un dongle resté bloqué à chaque cycle, attente bornée (~10 s) de
la ré-apparition du modem, no-op journalisé si le chemin est introuvable. Détails et bornes de sécurité dans
`docs/architecture.md` ; prérequis d'écriture sysfs dans `docs/deployment.md`.

### Mode USB du modem (émulation CD-ROM / `AT^SETPORT`)

Les dongles Huawei E3372 sont livrés en mode *stick* avec **émulation CD-ROM** :
interfaces `usb-storage` en plus des interfaces `option`/`huawei_cdc_ncm`, et un
`/dev/sr0` qui génère du bruit (probes `udisksd`, erreurs « medium ») sans être
utile. On retire cette émulation **au niveau du firmware** plutôt que côté OS
(pas de `/dev/sr0` du tout, déterminisme au reboot).

- Le dongle est identifié par son **IMEI** (`867028041311195` pour le dongle
  concerné ici), **jamais** par l'index `ttyUSB` (cf. plus haut) : après une
  ré-énumération les indices bougent.
- La commande est envoyée sur le port AT (`interface option`, `115200 8N1`).
  Grammaire : `AT^SETPORT="<émulation>;<interfaces>"` — avant le `;`, `A1`=CD-ROM,
  `A2`=SD, `FF`=aucune émulation ; après le `;`, `12`=**PCUI (AT)**, `16`=**NCM
  (netdev)**, `1`/`10`=PPP 3G/4G.
- Procédure : `AT^SETPORT?` (**noter la valeur**), `AT^SETPORT="FF;12,16"`,
  `AT^SETPORT?` (vérifier), puis `AT^RESET` (indispensable ; le dongle se
  ré-énumère seul en ~30 s, sans replug physique).
- Règle absolue : **toujours garder `12` (PCUI)** — sans lui, plus de port AT et
  donc plus moyen d'annuler (brick potentiel). Rollback :
  `AT^SETPORT="A1,A2;12,1,16,A1,A2"` + `AT^RESET`.
- Résultat attendu : interfaces réduites à `option`+`huawei_cdc_ncm`, `/dev/sr0`
  disparu, netdev NCM conservé. Appliqué le 2026-10-15 sur `867028041311195`
  (USB `1-1.2.4.3`) : `A1,A2;12,1,16,A1,A2` → `FF;12,16`, dongle `ONLINE` après
  reconvergence, proxy sain.
- ⚠️ Après `FF;12,16` il ne reste **qu'un** port PCUI : ModemManager s'en empare
  en exclusif, donc toute commande AT brute concurrente ne répond pas — il faut
  suspendre ModemManager (`systemctl stop ModemManager`) ou utiliser
  `mmcli --command=…` (mode debug) pour intervenir.

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

## Smartphone Uplink (tethering USB / RNDIS)

Un téléphone Android enrôlé en transport `adb` est un uplink comme un autre,
mais le bearer provient du **partage de connexion USB** (RNDIS) du téléphone,
pas d'un modem ModemManager. Le chemin est documenté ici car il réutilise
**exactement** les mêmes artefacts de table dédiée qu'un modem.

### Résolution de l'interface RNDIS (jamais de nom codé en dur)

La netdev RNDIS est nommée `enx<mac>` et ce nom **n'est pas stable**. Elle est
résolue par **sysfs** : le téléphone est identifié par son serial
(`/sys/bus/usb/devices/<id>/serial` == le serial ADB, `getprop ro.serialno`)
et la netdev est le device sous
`/sys/bus/usb/devices/<id>/…/net/` (`adapters/usb_sysfs_net.py`,
`find_net_interfaces_for_usb_serial`). L'interface est **ré-épinglee à chaque
cycle de reconciliation** (`workers/adb_soft_recovery.py`) : si le noyau a
renommé la netdev, le dongle est mis à jour sans reconnexion.

### Profil NetworkManager sans route par défaut

L'adresse du bearer est fournie par **NetworkManager** (`nmcli`, profil
`broxy4g-phone-<serial>`), pas par ModemManager :

- `ipv4.never-default yes` — le profil obtient une adresse IPv4 **sans
  jamais créer de seconde route par défaut globale** ;
- `ipv6.method disabled` ;
- `ipv4.route-metric 21000` — métrique élevée, la route du profil n'est
  jamais privilégiée.

Le **bail DHCP** (adresse + préfixe, passerelle lue dans `nmcli -f DHCP4 con
show`) est converti en configuration du bearer (`BearerIpConfig`, méthode
`dhcp`) — exactement la même structure que celle qu'expose ModemManager pour un
modem.

Deux rôles distincts, volontairement séparés (`_bearer_address`) :

- **valeur de l'adresse** = **vérité noyau** (`InterfaceAdapter.primary_ipv4` :
  `ip -o -4 addr show dev <iface> scope global`, première entrée `inet` dont les
  drapeaux ne contiennent pas `secondary`). C'est l'adresse que le noyau retient
  comme source pour une route sans `prefsrc`, donc celle que la règle source doit
  viser. L'ordre imprimé par `nmcli` n'est **pas** fiable : mesuré sur hôte réel
  (Galaxy A21S, RNDIS), `nmcli -g IP4.ADDRESS` imprimait
  `192.168.172.79/24 | 192.168.172.78/24` dans un ordre **variable** entre deux
  cycles, alors que le noyau sourçait `.78` (primaire) et non `.79`
  (`secondary`) — la règle source écrivait donc `.79` et ne couvrait pas
  l'adresse réellement utilisée ;
- **présence de l'adresse** = scopée au **profil** (`nmcli -g IP4.ADDRESS con
  show broxy4g-phone-<serial>`, `profile_ipv4`). Fail-closed : pas d'adresse sur
  **notre** profil → pas de bearer → le plancher anti-fuite est forcé, au lieu de
  construire la route et la règle source avec une adresse étrangère (la vue
  device, elle, renvoie une adresse même quand notre profil n'est pas actif).

Repli documenté : lecture noyau illisible → l'adresse du bail NM est utilisée
(jamais d'adresse inventée, jamais d'exception). Le parsing des sorties `nmcli`
(`adapters/network_manager_cli_output.py`) ne lève **jamais** d'exception :
multi-adresses, préfixe absent ou token parasite → première adresse valide ou
`None`.

#### Profil lié par MAC et suppression des connexions NM étrangères

Le profil `broxy4g-phone-<serial>` est lié à l'identité matérielle du netdev
(`802-3-ethernet.mac-address`, MAC lue dans sysfs via
`InterfaceAdapter.mac_address`), **pas** à `connection.interface-name`
(instable, cf. AGENTS.md). Repli tolérant : MAC illisible → comportement
antérieur (nom d'interface) + avertissement, jamais d'exception ;
`_verify_profile` vérifie alors la MAC (ou le nom d'interface en repli) et
refuse tout profil périmé.

- **Payload NM du profil (mesuré, Pi / Ubuntu 24.04, démon NM 1.46 / client
  nmcli 1.52)** : `mac-address` appartient au setting `802-3-ethernet`, de
  type D-Bus `ay` (tableau d'octets) : forme acceptée
  `'802-3-ethernet': {'mac-address': <[byte 0xNN, …]>}` (6 octets, hexadécimal
  minuscule). Mesures de rejet : `connection.mac-address` →
  `InvalidProperty: connection.mac-address: unknown property` ; forme chaîne
  dans `802-3-ethernet` → `can't set property of type 'ay' from value of
  type 's'` ; `@ay […]` ou `b'…'` sans `<…>` → erreur de parsing GVariant ;
  `<b'\x02…'>` accepté comme `ay` mais les échappements sont perdus (NM relit la
  chaîne ASCII). Un profil lié par MAC ne porte **ni** `connection.interface-name`
  **ni** de `mac-address` dans le setting `connection`.
- **Vérification de la liaison (mesuré, même hôte)** :
  `nmcli -g 802-3-ethernet.mac-address con show <profil>` rend la MAC en
  **majuscules avec les `:` échappés d'un antislash**
  (`02\:36\:0C\:7C\:14\:7A`) : la comparaison doit normaliser les deux côtés
  (retrait des antislashs + minuscules), sinon elle échoue
  systématiquement. Fail-closed : après écriture, une identité relue
  **positive et fausse** entraîne la **suppression** du profil écrit (un
  profil sans liaison valide est activable sur n'importe quelle interface
  ethernet) ; un échec **non concluant** (nmcli en erreur, champ de
  politique faux, identité vide) ne supprime rien. Limite connue
  (comportement antérieur, inchangé) : la branche « profil déjà présent
  mais non conforme » supprime le profil avant réécriture, y compris quand la
  non-conformité vient d'un échec transitoire de `nmcli`.

**Pourquoi** (défaut mesuré, Pi / Ubuntu 24.04 / NM 1.46) : une connexion NM
auto pré-existante liée à la même interface par `interface-name`
(ex. `Wired connection 1`, `autoconnect=yes`) reste **candidate** quand notre
profil apparaît — NM ne crée une connexion auto que si aucune connexion
compatible n'existe, mais n'en supprime jamais une pré-existante. À chaque
(ré)activation (notamment chaque cycle mode avion du reset) elle réinstalle sa
route par défaut dans la table `main` (`default … dev enx… metric 2023`, ~15 s
de fuite) ; le `ipv4.never-default` de *notre* profil n'y change rien (NM ne
supprime que la route automatique de sa propre connexion), et le garde-fou
`guard_default_route` doit alors la supprimer à chaque cycle (4 fois en 10 min
en recette). Le profil lié par MAC ne laisse plus cette connexion
pré-existante candidate par nom d'interface… mais elle peut toujours l'être
par MAC ou par activation manuelle : c'est le rôle de la suppression ci-dessous.

**Suppression** (`ForeignConnectionDeleter`,
`adapters/nm_foreign_connections.py`, appelée par
`SmartphoneUplinkService.bring_up` juste après `ensure_profile`) : toutes les
connexions NM dont `connection.interface-name` == notre interface **ou**
`802-3-ethernet.mac-address` == notre MAC, de type `802-3-ethernet`, et dont
l'uuid diffère de celui de notre profil (résolu par son nom), sont
**supprimées** par D-Bus `Settings.Connection.Delete` — idempotent (absence =
no-op silencieux), **jamais** une connexion active, une seule ligne de log
`ACTION ADMIN :` par suppression (nom + uuid + interface), aucune exception
propagée (NM en erreur → avertissement, le service continue).

**Pourquoi supprimer et non « neutraliser »** (mesuré, client nmcli 1.52.1 /
démon NM 1.46.0) :
- toute **écriture partielle** `nmcli con mod` échoue sous le décalage
  client/démon (`802-3-ethernet.mac-address-denylist: unknown property`) —
  impossible de poser `autoconnect=no`/`never-default=yes` ;
- la méthode D-Bus `Settings.Connection.Update` **remplace toutes les
  propriétés** : un map partiel détruirait (ou ferait rejeter) la connexion —
  à ne pas utiliser ;
- le **chemin d'objet** ne peut être reconstruit à partir du UUID (un tiret est
  illégal dans un chemin d'objet D-Bus : `… : not a valid object path`) : on
  prend le champ `DBUS-PATH` fourni par `nmcli` (jamais reconstruit) ;
- NM ne supprime **jamais** une connexion auto pré-existante quand notre profil
  apparaît ensuite, mais notre profil est désormais lié par **MAC** : tant
  qu'il existe, NM ne recrée pas de connexion auto → la suppression converge.

**Fail-safe** : si l'uuid de notre propre profil ne peut être résolu par son
nom, **aucune** suppression n'est tentée (on ne pourrait plus distinguer notre
connexion des étrangères). L'énumération n'utilise que des champs **autorisés**
(`NAME,UUID,TYPE,DBUS-PATH` ; `IFNAME` n'existe pas et ferait échouer la
commande entièrement) ; le parsing (`network_manager_cli_output.py`) tolère le
séparateur `:`, les champs vides et ne lève jamais d'exception.

- **Store NM adossé à netplan (mesuré, même hôte)** : les connexions NM sont
  persistées en `/etc/netplan/90-NM-<uuid>.yaml` et générées en
  `/run/NetworkManager/system-connections/netplan-NM-<uuid>.nmconnection` ;
  le store classique `/etc/NetworkManager/system-connections/` est **vide**.
  Toute modification d'une connexion NM réécrit le miroir netplan (mtimes
  observés). Conséquence pour l'isolation : supprimer une connexion
  étrangère via D-Bus (`Settings.Connection.Delete`) supprime **aussi** son
  YAML netplan et son keyfile `/run` (mesuré) → pas de résurrection par `netplan
  generate` au démarrage. `READONLY=no` sur ces connexions : l'écriture et la
  suppression D-Bus restent possibles.

**Garantie durable appliquée (hôte)** : `[main] no-auto-default=*` est installé
par `deploy/install.sh` dans
`/etc/NetworkManager/conf.d/98-broxy4g-no-auto-default.conf` (idempotent :
comparaison du contenu, redémarrage de NetworkManager **seulement** si le
contenu change). Mécanisme : NM ne **crée plus** de connexion automatique pour
un périphérique sans profil → la connexion volatile (route par défaut) à
l'origine de la fuite ci-dessus ne peut plus naître pendant la fenêtre
suppression/réécriture du profil d'un dongle. Ce qui **n'est pas** affecté :
les connexions persistées (profils netplan/cloud-init, profils de dongles de
l'application) et leurs routes. Mesuré le 2026-10-19 sur le Pi : sorties
publiques des 3 dongles et uplink de gestion inchangés après application +
redémarrage de NetworkManager, aucune nouvelle occurrence du garde-fou.
Condition d'application : sur un hôte dont l'uplink de gestion dépend d'une
connexion NM **auto-générée** (`Wired connection N`), il faut d'abord un
profil persisté pour cet uplink avant d'activer le réglage.

#### dhcpcd (Raspberry Pi OS) : second bail DHCP — vecteur distinct, le
`no-auto-default` ne suffisait pas

Raspberry Pi OS fait tourner **`dhcpcd` en plus de NetworkManager**
(mesuré : `dhcpcd 10.0.6`). Tant que `/etc/dhcpcd.conf` ne dénie que les
interfaces modem (`denyinterfaces dongle*`), `dhcpcd` prend un **second bail
DHCP** sur l'interface USB Ethernet/RNDIS du téléphone (`enx<mac>`, adresse
secondaire `192.168.172.79`) et **pose une route par défaut dans la table
`main`** via le dongle, ré-annoncée à chaque rebind. Le garde-fou applicatif
(`guard_default_route`) la supprime à chaque occurrence (log
`ACTION ADMIN : route par défaut de la table main via … — suppression`),
mais la fuite revient. Le réglage NM `no-auto-default=*` (paragraphe
ci-dessus) traite un **autre vecteur**, réel mais distinct (connexion
volatile générée par NM pendant la réécriture de profil) : **il ne suffisait
pas à lui seul** — les deux correctifs coexistent.

Preuve mesurée (2026-10-19, Pi ; heure locale = UTC+2 ; aucune activité
NetworkManager ni connexion NM étrangère à ces instants) : les trois
corrélations ajout/suppression correspondent exactement aux trois
occurrences du garde-fou :

```
Oct 19 10:47:56 dhcpcd: enx02360c7c147a: adding default route via 192.168.172.245
Oct 19 10:48:23 dhcpcd: enx02360c7c147a: pid 1751 deleted default route via 192.168.172.245   ← garde-fou
Oct 19 10:52:51 dhcpcd: enx02360c7c147a: adding default route via 192.168.172.245
Oct 19 10:53:29 dhcpcd: enx02360c7c147a: pid 6314 deleted default route via 192.168.172.245   ← garde-fou
Oct 19 11:17:55 dhcpcd: enx02360c7c147a: adding default route via 192.168.172.245
Oct 19 11:18:09 dhcpcd: enx02360c7c147a: pid 28470 deleted default route via 192.168.172.245  ← garde-fou
```

**Correctif** : la ligne canonique `denyinterfaces dongle* enx* wwan* wwx*`
dans `/etc/dhcpcd.conf`, couvrant toutes les conventions de nommage
Broxy4G : `enx*` = interface USB Ethernet/RNDIS dérivée de la MAC (identité
matérielle stable, cf. `AGENTS.md`), `wwan*`/`wwx*` = netdev modem/WiFi USB,
`dongle*` = interfaces renommées par fichiers udev `.link`.
Installation **idempotente** par `deploy/install.sh` (étape 8c : no-op si la
ligne est déjà présente, sauvegarde `/etc/dhcpcd.conf.broxy4g.bak`, hôte sans
dhcpcd → log et sortie sans échec). Application par **SIGHUP au manager**
(`kill -HUP "$(systemctl show -p MainPID --value dhcpcd)"`) : dhcpcd relit
sa configuration et **déconfigure** les interfaces qu'il ne gère plus (le
bail fautive, sa route par défaut et le process BPF disparaissent
immédiatement). **Jamais** `systemctl restart dhcpcd` : le restart lancerait
`dhcpcd -x` et déconfigurerait **toutes** les interfaces gérées, y compris
l'uplink de gestion.

Vérification sans régression après application (même hôte) : adresse
primaire `.78` intacte, table dédiée `10003` et règle
`from 192.168.172.78 lookup 10003` intacts, egress du dongle inchangé,
eth0/SSH intacts, garde-fou muet ensuite.

### Même table dédiée, mêmes artefacts

À partir du bearer ainsi résolu, les **mêmes** quatre artefacts de la table
dédiée sont posés de façon idempotente (`services/uplink_route_writer.py`) :
règle `fwmark` → table dédiée, route par défaut de la table
(`default via <gw> dev <iface>`), règle source **`from <adresse primaire du
noyau>`** et plancher anti-fuite `default unreachable`. L'adresse primaire de la
netdev RNDIS (celle que le noyau utilise comme source, voir ci-dessus) est la
source de la règle
`ip rule from <IP> lookup <table>` : le trafic du proxy (marqué par `fwmark`)
sort **par le téléphone**, et les sondes épinglées à cette source mesurent bien
cet uplink.

**Pourquoi la règle source compte ici** : les sondes de connectivité (ping,
IP publique, dont le gate de `DONE` du reset) sont épinglées à l'IP **source
du bearer** (`curl --interface <IP>`, `ping -I <IP>` —
`services/uplink_probe.py`). Pour qu'elles mesurent l'uplink du téléphone, ce
trafic doit suivre la table dédiée et sortir par la netdev RNDIS : sans la
règle `from <IP du bail>`, ces sondes suivraient la table `main` et mesureraient
l'uplink de l'hôte.

#### Effet mesuré d'un reset réussi (2026-10-19, Pi)

Profil réécrit lié par MAC (uuid **régénéré**), `ipv4.route-metric=21000`,
`never-default=yes`, `ipv6.method=disabled`. La table `main` conserve
**un seul défaut IPv4** (via l'interface de management) ; la table dédiée
du dongle garde **un seul** `default` via la passerelle du téléphone ;
règle `ip rule from <source> lookup <table>` ; l'IP publique de sortie
change effectivement (mesuré : `37.167.48.237` → `37.167.127.125`).

### Garde-fou : jamais de route par défaut de l'hôte via le téléphone

**Règle absolue (rappel)** : la table `main` et la route par défaut de l'hôte
ne sont **JAMAIS** modifiées. Un téléphone en partage USB peut, selon la
configuration Android, installer une route par défaut dans la table `main`
pointant vers sa propre netdev. C'est interdit ici : le filet de sécurité
`guard_default_route` (`services/smartphone_uplink.py`) liste les routes par
défaut de la table `main`, et si l'une d'elles passe par l'interface du
téléphone, elle est **journalisée (ACTION ADMIN) et supprimée** — à chaque
mise en ligne du profil **et à chaque cycle de réparation des routes d'uplink**
(`services/uplink_routes.py::_apply_adb`). Mesuré en recette : une bascule
NetworkManager (interface momentanément sans profil compatible) peut laisser
une telle route plusieurs minutes après la mise en ligne ; le garde-fou appelé
une seule fois ne l'a pas rattrapée.

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

Le reprovisionnement (donc la reconciliation) répare aussi automatiquement un
proxy dont `password_hash` est NULL — une ligne antérieure à la migration de la
colonne : le hash MD5-crypt n'étant pas récupérable, de nouveaux identifiants
sont générés et persistés. L'événement est journalisé en warning ; les clients
du proxy doivent être reconfigurés avec les nouveaux identifiants (le mot de
passe n'est jamais re-renvoyé par l'API). La réparation se faisant hors API,
ces identifiants ne sont affichés à personne : un opérateur qui en a besoin
déclenche ensuite une rotation explicite
(`POST /api/v1/proxies/{proxy_id}/rotate`), qui renvoie le nouveau couple une
seule fois.

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

## Pools de proxies

Un pool de proxies (cf. `docs/proxy-pools.md`, `docs/architecture.md` →
*Proxy Pools* et `docs/api.md` → *Proxy Pools*) expose plusieurs proxies
derrière un port unique. Il n'apporte **aucun** artefact réseau de son propre
fait :

- **Plage de ports dédiée `31301-31399`** (`POOL_PORT_START` /
  `POOL_PORT_END`, `domain/proxy_pool.py`), disjointe de la plage des proxies
  (`31201-31299`) et du port de l'API (`8080`). L'allocation est faite par
  `PoolPortAllocator` (`services/pool_ports.py`) : elle ignore les ports déjà
  pris par un pool, par un proxy, ou publiés par Docker dans cette plage. Le
  port est persisté à la création et **jamais réattribué dynamiquement** : un
  redémarrage ne change jamais l'URL communiquée aux clients.
- **Même interface d'écoute que les proxies** : le bind suit
  `ProxyListenService.resolve_bind_ip` (l'IPv4 de `proxy.listen_interface`,
  `0.0.0.0` si aucune interface n'est configurée). Interface absente ou sans
  IPv4 ⇒ le pool n'est **pas exposé** ; l'erreur est remontée dans
  `listen_error` du statut API (graceful, comme l'ingress des proxies).
- **Aucune modification nftables** : le gateway de pool tourne **dans le
  processus API** (réseau hôte, pas de conteneur), et chaque connexion
  sortante vers un membre cible l'adresse **interne** du membre
  (`172.28.0.x:3128`, sur le pont `broxy4g-net`). C'est le **proxy membre** —
  déjà épinglé à son dongle par `ip rule` / marque firewall et couvert par
  les règles `proxy_mark` / `proxy_nat` / `proxy_filter` — qui porte
  l'isolation. Le trafic du pool ne crée donc **ni route, ni règle, ni table
  par dongle** : les règles nftables par proxy restent la seule autorité
  d'egress.
- **Un pool ne publie jamais le port LAN d'un proxy** et ne contourne jamais
  les règles de binding : un client du pool s'authentifie avec les
  identifiants **du pool** (Basic, en-tête `Proxy-Authorization`), puis le
  gateway réinjecte les identifiants **du membre** dans la requête envoyée au
  membre. Les deux couples ne sont jamais confondus, ni exposés l'un par
  l'autre.
- **Source autorisée côté membre** : le gateway (processus API, réseau hôte)
  joint `172.28.0.x:3128` avec l'adresse de la passerelle du pont
  (`172.28.0.1`). Cette IP fait partie des sources hôte par défaut de
  `render_3proxy_config` (`DEFAULT_HOST_SOURCES` : `127.0.0.1` +
  `DOCKER_GATEWAY`), donc l'ACL `allow …/deny *` du membre laisse passer le
  pool sans réglage supplémentaire. Ne jamais retirer `172.28.0.1` des sources
  hôte d'un proxy membre.
- **Sortie de rotation pendant un reset** : quand un dongle entre en reset
  (`RESETTING` ou verrou de reset tenu), ses proxies sortent immédiatement de
  la rotation (règle d'éligibilité, ré-évaluée à chaque connexion) **sans
  couper les tunnels déjà établis** ; la connexion en cours tombe d'elle-même
  quand le reset ferme le conteneur.

## Isolation d'un proxy pendant un reset de dongle

Le reset d'un dongle **met le conteneur proxy en pause**
(`docker compose down --remove-orphans`) puis le **reprend**
(`docker compose up -d`), `enabled` étant conservé. Défaut mesuré
(2026-10-19, Pi / Ubuntu 24.04) : la réconciliation des proxies (boucle
30 s) ne tenait pas compte du reset ; voyant le conteneur absent, elle
lançait un second `docker compose up` concurrent avec la reprise du
reset → `Error response from daemon: Conflict. The container name
"/broxy4g-proxy-<uuid>" is already in use by container "<id>"` → proxy
`CRASHED`, reset en `ERROR` alors que l'uplink était sain (la sonde
d'IP publique n'était jamais atteinte).
Correctifs : (1) `ProxyReconciler` applique **le même interlock que le
chemin dongle** — un proxy de dongle est ignoré si le dongle est
`RESETTING` **et** que le verrou de reset est réellement tenu ; un
`RESETTING` **sans** verrou (état résiduel post-crash/reboot) est restauré
normalement ; (2) les opérations `docker compose` (`up`/`down`/`restart`/
`remove`) sont **sérialisées en single-flight** au niveau du processus, et
en cas de conflit « already in use by container », `up` **adopte** le conteneur
uniquement si **exactement un** conteneur porte les labels
`com.docker.compose.project=<projet>` **et** `com.docker.compose.service=proxy` **et**
est à l'état `running` ; sinon l'erreur d'origine est relevée.
Preuve de recette après correctif : journal `Proxy <id> ignoré : reset du dongle
<id> en cours` pendant le reset, reset `DONE`, aucun conflit, proxy `RUNNING`.

## Auto-réparation du bridge proxy et sonde de santé

### Création et auto-réparation du bridge du réseau proxy

Le réseau Docker partagé `broxy4g-net` est créé et géré **par
l'application** (`ProxyNetworkService.ensure_ready`,
`services/proxy_network.py` ; `DockerNetworkAdapter.create_network`,
`adapters/docker_network.py`) : les fichiers compose le déclarent
`external: true`. `ensure_ready` est appelée avant tout `docker compose up`
(provisionnement d'un proxy) et à chaque cycle de réconciliation, sous un
verrou single-flight, de façon **idempotente** :

- réseau **absent** (premier démarrage, suppression accidentelle, reboot) →
  `docker network create` avec sous-réseau `172.28.0.0/16`, passerelle
  `172.28.0.1`, masquerade Docker désactivé ; « already exists » = succès
  silencieux, toute autre erreur est remontée. L'action d'administration est
  journalisée ;
- réseau **présent** → se réduit à `ensure_gateway` : le device bridge du
  réseau porte bien son IP passerelle (`172.28.0.1` + préfixe `16`) ;
  adresse déjà présente → aucune écriture.

Un pont existant n'est **jamais** supprimé, modifié ni recréé par
l'application : il porte potentiellement des conteneurs actifs. Ne jamais
`flush` ni supprimer d'adresse sur ce bridge.

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

Le réseau `broxy4g-net` étant créé par l'application (voir ci-dessus),
l'option `com.docker.network.bridge.enable_ip_masquerade: "false"` est
appliquée à la **création** (`DockerNetworkAdapter.create_network`) :

```
docker network create --driver bridge \
  --subnet 172.28.0.0/16 --gateway 172.28.0.1 \
  --opt com.docker.network.bridge.enable_ip_masquerade=false broxy4g-net
```

Les fichiers compose de chaque proxy ne déclarent le réseau que comme
externe (`domain/proxy_compose.py`) — Compose ignorerait silencieusement
`driver`/`ipam` sur un réseau externe :

```yaml
networks:
  broxy4g-net:
    external: true
    name: broxy4g-net
```

**Pourquoi** : sans cette option, Docker installe son propre masquerade
POSTROUTING sur `172.28.0.0/16` ; il SNATe le trafic fuyant du sous-réseau
proxy et le rend effectivement routable sur l'uplink de l'hôte. Notre chaîne
`proxy_nat` (masquerade conditionné à l'interface du dongle lié :
`oifname "<iface>" ip saddr 172.28.0.0/16 masquerade`) doit rester le **seul**
SNAT légitime.

**Mise à jour d'une installation existante** : Docker n'applique cette option
qu'à la **création** du réseau, et l'application ne modifie jamais un réseau
existant. Si un réseau créé par une ancienne version (compose ou application)
n'a pas l'option :

1. Arrêter les proxies (arrêter/stopper les conteneurs) ;
2. `docker network rm broxy4g-net` ;
3. Relancer / réconcilier — `ProxyNetworkService.ensure_ready` recrée le
   réseau, avec l'option appliquée ;
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