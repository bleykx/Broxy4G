# Broxy4G Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                                                             │
│                                    ┌──────────────────────────────────────────────────────────────────────┐                             │
│                                    │                    Broxy4G Application                      │                             │
│                                    │                                                               │                             │
│                                    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │                             │
│                                    │  │   API Layer   │  │ Domain Layer  │  │ Services  │  │                             │
│                                    │  │               │  │               │  │           │  │                             │
│                                    │  │  FastAPI      │  │  Models       │  │  Business │  │                             │
│                                    │  │  Routes         │  │  State        │  │  Logic    │  │                             │
│                                    │  │  Serializers    │  │  Machines │  │           │  │                             │
│                                    │  └─────────────┘  └─────────────┘  └─────────────┘  │                             │
│                                    │                                                               │                             │
│                                    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │                             │
│                                    │  │  Workers  │  │ Repositories│  │ Adapters    │  │  Frontend │  │                             │
│                                    │  │               │  │               │  │           │  │  (System)   │  │                             │
│                                    │  │  Discovery      │  │  DB Access  │  │  Shell,     │  │             │  │                             │
│                                    │  │  Reconciliation │  │               │  │  D-Bus,     │  │             │  │                             │
│                                    │  │               │  │               │  │  Docker,    │  │             │  │                             │
│                                    │  │               │  │               │  │  Network    │  │             │  │                             │
│                                    │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │                             │
│                                    └───────────────────────────────────────────────────────────────┘                             │
│                                                                                                                             │
│                                    ┌───────────────────────────────────────────────────────────────────────────────────────────────┐ │
│                                    │                                      Hardware                                      │ │
│                                    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │ │
│                                    │  │   Dongle 1    │  │   Dongle 2    │  │   Dongle 3    │  │   Dongle N    │  │   Proxy     │  │ │
│                                    │  │  (4G/5G)    │  │  (4G/5G)    │  │  (4G/5G)    │  │  (4G/5G)    │  │  (3proxy)   │  │ │
│                                    │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │ │
│                                    └───────────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                                                             │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Layer Descriptions

### api/
Contains FastAPI routes and request/response serialization. This is the entry point for HTTP requests.

### domain/
Contains business models, state machines, and business rules. This layer defines the core logic of the application.

### services/
Contains the business logic for specific use cases. These services orchestrate the interaction between domain models and adapters.

### adapters/
Encapsulates interactions with system components like ModemManager, NetworkManager, Docker, nftables, etc. These are the bridges to system-level operations.

### repositories/
Handles data persistence operations. These are the only classes that directly interact with the database.

### workers/
Handles background tasks such as discovery and reconciliation of dongles.

### frontend/
Contains the React/Vite frontend application.

## APN Resolution

L'APN par défaut d'un dongle est déduit automatiquement, pour ne pas dépendre d'une
saisie manuelle. Le domaine expose `resolve_apn(imsi, operator_id, operator_name,
providers)` et le protocole `ApnResolver`.

- `adapters/mbpi_source.py` (`MbpiApnSource`) charge la base **MBPI**
  (`mobile-broadband-provider-info`, `serviceproviders.xml`, licence CC-PD, vendored
  dans `src/broxy4g/data/`) et l'expose via `ApnResolver`.
- La résolution privilégie le couple `(mcc, mnc)` dérivé de l'IMSI/SIM, avec repli sur
  l'`operator_id` 3GPP puis désambiguïsation par `operator_name` (MNC partagés, p. ex.
  Lebara/SFR en `208/10`).
- `_PREFERRED_APNS` surcharge l'ordre MBPI pour les opérateurs dont le premier APN
  internet est une offre pro (p. ex. Bouygues).
- Le résultat est exposé comme `suggested_apn` sur les dongles découverts et utilisé
  lors de l'enrôlement **uniquement si l'utilisateur ne fournit pas d'APN**.

## Data Flow

1. HTTP request arrives at API layer (FastAPI routes)
2. Request is validated and serialized
3. Service layer processes the business logic
4. Adapters interact with system components
5. Repository persists data changes
6. Response is returned to client

## State Machines

### DongleState Transitions

```
DISCOVERED ──(enroll)───► UNENROLLED ──(connect)───► CONNECTING
                              │
                              └─(configure)───► CONFIGURING ──(connect)───► ONLINE
                              │
                              └─(disconnect)───► OFFLINE
                              │
                              └─(reset)───► RESETTING
                              │
                              └─(error)───► ERROR

ONLINE ──(degraded)───► DEGRADED
ONLINE ──(disconnect)───► OFFLINE
DEGRADED ──(connect)───► ONLINE
DEGRADED ──(disconnect)───► OFFLINE

Reset (récupération) : le reset est une action de **récupération**, autorisée
depuis ONLINE, DEGRADED, OFFLINE et ERROR. Il passe le dongle en RESETTING,
coupe le bearer, effectue un **vrai reset modem USB** (`mmcli -m <path>
--reset`, ré-énumération : le modem disparaît ~15-40 s puis réapparaît sous un
nouveau chemin, interface parfois renommée, retrouvé par IMEI) puis reprend la
connexion (RESETTING ──► CONNECTING). Seul ce reset modem renouvelle l'IP
publique. Les états pré-connexion (DISCOVERED, UNENROLLED, CONFIGURING) sont
interdits : le reset renvoie alors `ERROR` avec un message métier propre.

L'état reste **RESETTING pendant tout le reset** : le teardown de l'uplink
(déconnexion bearer + down de l'interface + purge des règles/routes) est
exécuté sans passer par OFFLINE, si bien que la fenêtre modem-absent ne casse
pas l'interlock ci-dessous et la réconciliation ne tente jamais de `connect`
intercalé. En échec, RESETTING ──► ERROR (`dongle.fail`).

Pendant le reset, le proxy du dongle est **mis en pause** puis recréé à la
reconnexion : le conteneur 3proxy est épinglé au dongle (table de routage dédiée
+ marque firewall), donc après un reset modem l'ancien bearer et ses routes
n'existent plus et le conteneur doit être reconstruit pour sortir par la nouvelle
session. Son **état désiré** (`enabled`) est conservé d'un bout à l'autre : un
proxy désactivé par l'opérateur le reste. Un reset en échec ou expiré laisse le
conteneur arrêté mais `enabled` à vrai — la réconciliation le restaure dès que
l'uplink redevient utilisable. Aucun proxy n'est démarré sur un uplink mort.

**Garde anti-tempête du reset** : un reset force une ré-énumération USB
destructrice (~15-50 s de panne), donc les resets répétés du même dongle sont
amortis par une garde par dongle (services/reset_throttle.py) : un **cooldown**
minimal entre deux resets exécutés (défaut 60 s) et un **plafond** de resets par
fenêtre glissante (défaut 5 / 600 s). Une demande dans le cooldown renvoie
`PENDING` (message cooldown), un dépassement du plafond renvoie `ERROR`
(message commençant par « reset refusé ») ; les statuts restent
`DONE` / `ERROR` / `PENDING` (le `LOCK` du verrou manuel, ci-dessous, s'y
ajoute). Le refus est évalué **avant** la prise de verrou
et ne touche **jamais** le matériel (pas de reset modem, pas de changement
d'état du dongle) ; l'historique est en mémoire (un redémarrage du service le
réinitialise).

**Gate d'IP publique et fenêtre post-DONE** : `DONE` n'est renvoyé que si la
sonde d'IP publique (épinglée à l'IP source du bearer, verdict tri-état — voir
`UplinkProbe`) atteint `OK` après la reconnexion, via une boucle d'attente
bornée. Si elle ne passe jamais `OK`, le reset renvoie `ERROR` **sans marquer
le dongle `ERROR`** (il reste `ONLINE`) : le marquer déclencherait une
ré-énumération USB destructive (usb recovery) sur un lien qui fonctionne.
Le critère est le verdict de sonde, pas le delta d'IP (caveat CGNAT). Après un
`DONE`, une fenêtre de répit par dongle en mémoire (domain/post_done_window.py,
défaut 15 s) renvoie `PENDING` à toute nouvelle demande sans re-resetter le
matériel ; elle est (re)posée seulement par un `DONE` (un refus ne la
ré-horodate jamais) et est distincte du verrou et du throttle.

**Verrou manuel du reset** : le reset single (POST/GET, pas le batch) accepte
un paramètre de query optionnel `?lock=<secondes>` (borne de sécurité fixe :
86400 s = 24 h, pas de variable d'environnement). À la fin d'un reset
réellement `DONE`, le verrou manuel par dongle en mémoire
(domain/reset_lockout.py) est armé pour `lock` secondes : pendant ce délai,
toute demande de reset renvoie le statut métier exact `LOCK` (message avec le
temps restant), **sans toucher le proxy** — le conteneur 3proxy continue de
servir, aucune action nftables/réseau/conteneur. Le verrou n'est armé que sur
un `DONE` (jamais sur un refus, un `PENDING` ou un `ERROR`), est levé par
expiration ou par l'endpoint authentifié `GET /api/v1/dongles/{id}/unlock`
(idempotent, `DONE`/`ERROR` en texte brut, no-store), et est perdu au
redémarrage du service. Ordre des vérifications : verrou manuel → throttle →
fenêtre post-DONE → verrou de reset ; après un unlock, le throttle et la
fenêtre post-DONE restent applicables.

ONLINE    ──(reset)───► RESETTING
DEGRADED  ──(reset)───► RESETTING
OFFLINE   ──(reset)───► RESETTING
ERROR     ──(reset)───► RESETTING
RESETTING ──(reconnect)──► CONNECTING
```

> **Interlock de réconciliation :** la reconciliation ignore **totalement** un
> dongle `RESETTING` **si le verrou de reset est réellement tenu** (reset en
> cours : connexion, proxy et connectivité sont sautées). Un `RESETTING`
> **sans verrou** est un état résiduel post-crash ou post-reboot : la
> restauration est relancée. Le reset est borné dans le temps
> (`reset_timeout`) et le verrou est libéré dans un bloc `finally` même en cas de
> timeout/annulation. L'application de l'isolation nftables est sérialisée
> (single-flight) pour éviter tout entrelacement de `nft -f` sous reset batch.

> **Auto-réparation des routes d'uplink :** à chaque cycle, après la
> restauration de connexion et avant la vérification de connectivité, la
> reconciliation (re)pose les routes d'uplink du dongle
> (`services/uplink_routes.py`, `UplinkRouteService`) à partir de son bearer
> courant — règle fwmark, route par défaut de la table dédiée et règle
> source. Cette (re)pose est idempotente (aucune écriture si tout est
> conforme) et **ne reconnecte jamais le modem** : une route écrasée sur un
> dongle déjà `ONLINE` (dont `connect()` est sauté) est détectée et réparée
> au cycle suivant. C'est la même implémentation que `connect()`.

> **Hystérésis asymétrique de `DEGRADED` :** la vérification de
> connectivité de la reconciliation (`services/connectivity_verdict.py`,
> arbitre pur `domain/degraded_arbiter.py`) décide de l'entrée/sortie de
> `DEGRADED` à partir de verdicts **tri-état** des sondes (`OK` / `FAILED` /
> `NOT_RUN` — `NOT_RUN` = pas d'IP source sur l'interface, sonde non
> exécutable) :
>
> - **Sortie immédiate** : IP publique `OK` ⇒ retour à `ONLINE` au cycle
>   suivant, quel que soit l'historique.
> - **Entrée immédiate** : blackhole concordant (IP `FAILED` **et** ping
>   != `OK`) ⇒ `DEGRADED` au cycle suivant, sans hystérésis.
> - **Sinon** (`NOT_RUN`, ou IP `FAILED` avec ping `OK`) : échec faible ;
>   `DEGRADED` seulement après `min_consecutive` échecs faibles consécutifs
>   (paramètre du constructeur de `ReconciliationWorker`, défaut **2**). Le
>   compteur est **en mémoire, par dongle** : un redémarrage du service le
>   réinitialise.
>
> Un `DEGRADED` dont le **bearer est encore vivant** (IP du bearer présente
> sur l'interface) ne déclenche plus `connect()` — qui ferait
> `flush_ipv4` + `assign_address` et changerait l'IP de bearer à chaque
> cycle : seules les routes d'uplink sont réparées. `connect()` n'est
> relancé que si le bearer est mort, absent ou périmé.
>
> **Risque résiduel assumé** : l'état peut tarder d'un cycle sur un lien
> intermittent ; l'isolation et le routage ne dépendent jamais de la sonde.
> L'isolation réseau (nftables) et le routage (tables, fwmark, plancher)
> sont (re)posés à chaque cycle indépendamment du verdict de connectivité.

### Récupération douce par ré-énumération USB

Un dongle **enrôlé** bloqué en `ERROR` ou `OFFLINE` avec un bearer **PPP**
(port `net` absent chez ModemManager, donc aucun netdev) ne peut pas se
rétablir en reconnectant : `connect()` échoue à chaque cycle. La
reconciliation tente alors, **avant** la restauration de connexion, une
récupération douce : désautorisation puis ré-autorisation du périphérique
USB
du dongle (écriture `0` puis `1` du fichier sysfs `authorized`), ce qui
force le noyau à le ré-énumérer proprement — sans reboot ni reset
applicatif (`services/usb_recovery.py`, `UsbRecoveryService`).

Règles métier :

- Éligible uniquement depuis `ERROR` ou `OFFLINE` (jamais `RESETTING`,
  `CONFIGURING`, `DISCOVERED`, `UNENROLLED`, `CONNECTING`, `ONLINE`,
  `DEGRADED`) ; jamais si le **verrou de reset** est tenu pour le dongle.
- **Au plus une tentative par dongle et par cycle de reconciliation** : le
  compteur est remis à zéro par `begin_cycle()` au début de
  `reconcile_all()`.
- **Cooldown inter-cycles** (backoff temporel) : après une tentative, la
  ré-énumération d'un même dongle n'est retentée qu'au bout de
  `cooldown_seconds` (`BROXY4G_USB_RECOVERY_COOLDOWN_SECONDS`, défaut
  **300 s**). Sans ce bornage, un dongle resté `ERROR` serait ré-énuméré à
  **chaque** cycle (le modem revient sur USB en <1 s mais ModemManager met
  plus de temps à le ré-enregistrer, si bien que `connect()` échoue de
  nouveau). Le cooldown **survit aux cycles** (`begin_cycle()` ne le remet pas
  à zéro) et n'est gardé qu'en **mémoire** : un redémarrage du processus
  autorise une tentative immédiate. Les cooldowns sont **par dongle**
  (indépendants).
- **Attente bornée** (timeout par défaut ~10 s) de la ré-apparition du modem
  chez ModemManager (disparition puis retour) ; au timeout : WARNING
  journalisé, pas de boucle, le cycle suivant décide.
- **Idempotence par étape** : aucune écriture ne réécrit une valeur déjà
  présente ; une tentative ne laisse jamais `authorized` à `0` sans le
  signaler (échec persistant de ré-autorisation journalisé).
- Chemin USB introuvable ⇒ **no-op journalisé** (WARNING), jamais
  d'exception : le cycle de reconciliation n'est jamais cassé.

Le chemin du périphérique USB est celui **rapporté par ModemManager**
(champ `device` de `mmcli -m`, section System), porté par
`ModemInfo.device_path` : il existe même sans netdev (cas PPP).

**Résolution de l'identifiant de device** (`adapters/usb_sysfs_path.py`,
source unique, partagée avec la lecture du serial USB) : c'est le composant
**le plus profond** du chemin qui est un identifiant sysfs USB
(`^\d+-[\d.]+$`), suffixe d'interface `:N.M` ignoré. Les topologies réelles
sont souvent **imbriquées** (Raspberry Pi : le dongle derrière des hubs,
`…/usb1/1-1/1-1.2/1-1.2.4`) — l'identifiant visé est toujours le dongle,
**jamais un hub parent**, et il n'y a jamais de repli vers un composant
parent.

Toute écriture sysfs passe par l'adaptateur testable
`adapters/usb_device.py` (`UsbDeviceAdapter`, `RealUsbDevice`) : la cible est
bornée — realpath du répertoire device devant (a) avoir pour dernier
composant l'ID visé et être situé dans l'arborescence d'un contrôleur
(`…/usbN/…`), (b) contenir bien le fichier `authorized`, et (c) **ne pas
être un hub** (`bDeviceClass` = `09`, refusé avec un WARNING — le
désautorisé ferait tomber tous les périphériques en aval). Si
`bDeviceClass` est illisible, le contrôle hub est sauté (DEBUG) et la borne
de chemin décide seule. La politique (éligibilité, compteur par cycle,
attente bornée) vit dans le service ; le worker `workers/reconciliation.py`
ne fait que l'orchestrer.

### ProxyState Transitions

```
CREATED ──(start)───► STARTING
          ──(stop)───► STOPPED
          ──(remove)───► REMOVED

STARTING ──(success)───► RUNNING
           ──(failure)───► CRASHED

RUNNING ──(stop)───► STOPPING
          ──(remove)───► REMOVED

STOPPING ──(success)───► STOPPED
          ──(failure)───► CRASHED

CRASHED ──(restart)───► STARTING
          ──(remove)───► REMOVED
```

### Proxy Shared Docker Network

Tous les conteneurs 3proxy partagent un unique pont Docker
(`broxy4g-net`, `172.28.0.0/16`). Ce pont est créé et géré **par
l'application** : `ProxyNetworkService.ensure_ready` (`services/`) crée le
réseau s'il est absent (`DockerNetworkAdapter.create_network`,
`adapters/`, idempotent) et répare la passerelle du bridge ; les fichiers
compose de chaque proxy le déclarent `external: true`. Un pont existant
n'est jamais supprimé ni recréé. Détails : `docs/networking.md`
(« Création et auto-réparation du bridge du réseau proxy »).

## Identity Strategy

Dongles are identified by a stable hardware identity, in order of preference:
1. IMEI (International Mobile Equipment Identity)
2. USB Serial Number
3. ADB Serial (smartphone dongles, e.g. a phone with no readable IMEI/ICCID)
4. ICCID (Integrated Circuit Card ID)

The full identity material is persisted, including the ADB serial
(`identity_adb_serial` column), so an ADB-only identity survives a
database round trip (reboot, restart, re-enrollment) without losing its
`identity_key`.

This ensures that a dongle can be consistently identified even after reboots, USB unplugging/replugging, or other system changes.

L'application **ne renomme jamais** l'interface réseau d'un dongle : un
renommage `ip link set … name` fait perdre à ModemManager le port réseau
`(net)` du modem (dégradation en session PPP sans IPv4) et n'est pas
persistant. Le nom d'interface (assigné par le noyau : `wwan0`, `wwx0`, …)
est résolu à la lecture via sysfs (netdev rattaché au périphérique USB du
modem) et persisté sur le dongle comme simple confort d'exploitation — il
n'est jamais utilisé pour l'identifier.

## Device Transports

Un « dongle » est une **entité unique** (`domain/dongle.py`, `Dongle`) portant
un champ `transport` (`domain/transport.py`, `Transport`) à deux valeurs :
`MODEM` (valeur par défaut) ou `ADB` (téléphone Android en partage de
connexion USB). Il n'existe pas de ressource parallèle « téléphone » : même
entité, même machine à états, mêmes routes API, mêmes tables de routage —
seul le chemin d'uplink et la stratégie de récupération diffèrent selon le
transport.

### Identité

L'ADB serial entre dans la stratégie d'identité de la même façon que les
autres identifiants : priorité **IMEI > numéro de série USB > ADB serial >
ICCID** (`domain/identity.py`, `primary_key`), et le ADB serial est **persisté**
(colonne `identity_adb_serial`) : une identité ADB-only survit donc aux
allers-retours base de données avec sa clé primaire intacte. Pour un téléphone,
l'ADB serial est en pratique toujours la clé primaire (pas d'IMEI ni d'ICCID
exposés). L'application n'a pas besoin de l'IMEI du téléphone pour l'identifier.

### Découverte

Le worker de découverte interroge `adb devices -l` (`adapters/adb.py`,
`list_devices`) et n'annonce que les appareils dont l'état est `device` :
les états `unauthorized` et `offline` sont **ignorés silencieusement**
(`services/discovery.py`, `_discover_phones`). Chaque téléphone annoncé est
un dongle découvert avec `transport=adb`, `identity_adb_serial` = serial ADB,
`interface_name=None` (le netdev n'existe que si le partage USB est actif) et
`model` (champ ADB `model:`, sinon la propriété `ro.product.model`). Un
échec du serveur ADB (absent, binaire manquant) est journalisé et n'annonce
aucun téléphone, sans casser le cycle.

### Enrôlement

- Un téléphone **gère son propre APN** : l'enrôlement d'un dongle ADB ne
  résout **aucun** APN (`services/enrollment.py`) — la base MBPI n'est
  consultée que pour les transports modem.
- L'enrôlement **déclenche automatiquement** l'activation du partage USB
  (tethering RNDIS) : le service passe le dongle `CONFIGURING`, puis
  `CONNECTING`, puis `ONLINE` si le bearer est établi.
- Un échec de cette activation **ne fait jamais échouer l'enrôlement** : le
  dongle est rechargé depuis le dépôt et reflète son état final réel
  (`ERROR` avec `last_error` si la connexion a échoué, `CONFIGURING` sinon) ;
  la réconciliation relancera la connexion au cycle suivant.

### Cycle de vie et récupération par transport

Les transitions d'état sont **identiques** aux deux transports (`CONNECTING →
ONLINE / ERROR`, etc.). Les différences portent sur le chemin de connexion et
la récupération :

- **Connexion** (`services/smartphone_connection.py`) : activation du
  tethering (`svc usb setFunctions rndis`, vérification bornée de
  `sys.usb.state` — cf. `docs/deployment.md`), résolution du netdev RNDIS par
  sysfs (jamais de nom codé en dur), profil NetworkManager sans route par
  défaut, bail DHCP converti en configuration du bearer, puis les mêmes
  artefacts de table dédiée qu'un modem (voir `docs/networking.md`).
- **Récupération douce** (`workers/adb_soft_recovery.py`) : un dongle ADB ne
  subit **JAMAIS** de ré-énumération USB (le bus USB du téléphone n'est pas le
  problème ; c'est le netdev RNDIS qui est instable). La récupération est une
  **attente bornée puis une relance bornée du serveur adb** :
  - `adb wait-for-device` **borné** (timeout par défaut 30 s) : attendre que
    l'appareil revienne à l'état `device`. S'il revient, tout est bon, **aucune
    relance**.
  - Si l'attente échoue, **escalade d'une seule relance bornée du serveur adb**
    (`RealAdb.restart_server()` = `adb kill-server` (échec toléré),
    `adb start-server` (timeout), puis `adb reconnect offline`), **puis
    re-attente** du device. La relance est journalisée comme action
    d'administration (`ACTION ADMIN`, même convention que le ré-épinglage
    d'interface). Le WARNING d'échec n'est émis qu'**après** l'échec de la
    tentative escaladée ; aucune exception ne s'échappe du worker.
  - **Pourquoi relancer le serveur (cas prouvé sur matériel, reboot)** : le
    serveur adb est démarré par l'application au boot, **avant** l'énumération
    USB du téléphone, et reste alors **aveugle** (`adb devices` vide) alors que
    l'interface ADB est bien présente dans sysfs. Un serveur **neuf**, démarré
    après l'énumération, voit immédiatement le device. Sans relance, toute
    commande ADB (tethering, reset, disconnect) resterait morte — alors que le
    chemin de données (routes/table/fwmark/proxy) est restauré par le reste de
    la reconciliation (priorité n°2 : reconciliation fiable après reboot).
  - **Cooldown en mémoire par serial** (défaut 60 s, borne `restart_cooldown`,
    horloge injectable) : une relance échouée ou réussie ne retentée pas à
    **chaque** cycle de reconciliation sur un device réellement absent, ce qui
    éviterait une tempête de `adb kill-server`/`start-server`. Distinct de
    l'attente bornée ; gardé en mémoire seulement (un redémarrage du processus
    autorise une relance immédiate).
  - L'interface est en parallèle **ré-épinglee via sysfs** à chaque cycle (le
    netdev peut changer de nom sans ré-énumération). Le modem, lui, conserve la
    ré-énumération USB (`UsbRecoveryService`) documentée plus haut.

### Reset d'un téléphone

Le reset est une **action de récupération** exactement comme pour un modem
(mêmes états d'origine, mêmes statuts métier) ; seule la mécanique diffère :

- Pas de ré-énumération USB : le reset coupe le profil NetworkManager puis
  effectue un **double cycle mode avion**
  (`cmd connectivity airplane-mode enable/disable`, deux fois) pour forcer une
  nouvelle session cellulaire, attend le retour de l'appareil
  (`adb wait-for-device` borné), puis relance la connexion complète.
- `DONE` a **exactement** le même sens : l'IP publique est **vérifiée** par la
  sonde (verdict `OK` après reconnexion, boucle d'attente bornée) — pas
  simplement la reconnexion du téléphone. Si la sonde ne passe jamais `OK`, le
  reset renvoie `ERROR` **sans marquer le dongle `ERROR`** (il reste `ONLINE`)
  : le marquer déclencherait une récupération sur un lien qui fonctionne.
- `PENDING` si un reset est déjà en cours (verrou), dans le cooldown ou la
  fenêtre post-`DONE` ; `ERROR` en échec ou timeout. Le verrou est libéré dans
  un bloc `finally`. Rien d'autre ne change : verrou, throttle anti-tempête et
  fenêtre post-`DONE` s'appliquent identiquement au deux transports.