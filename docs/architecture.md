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
  `reconcile_all()` ; si le dongle reste bloqué, le cycle suivant
  (30 s) réessaie.
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

## Identity Strategy

Dongles are identified by a stable hardware identity, in order of preference:
1. IMEI (International Mobile Equipment Identity)
2. USB Serial Number
3. ICCID (Integrated Circuit Card ID)

This ensures that a dongle can be consistently identified even after reboots, USB unplugging/replugging, or other system changes.

L'application **ne renomme jamais** l'interface réseau d'un dongle : un
renommage `ip link set … name` fait perdre à ModemManager le port réseau
`(net)` du modem (dégradation en session PPP sans IPv4) et n'est pas
persistant. Le nom d'interface (assigné par le noyau : `wwan0`, `wwx0`, …)
est résolu à la lecture via sysfs (netdev rattaché au périphérique USB du
modem) et persisté sur le dongle comme simple confort d'exploitation — il
n'est jamais utilisé pour l'identifier.