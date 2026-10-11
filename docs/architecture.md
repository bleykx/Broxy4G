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