# Pools de proxies

## Objet

Un **pool** regroupe plusieurs proxies 3proxy (chacun rattaché à un dongle ou à un
uplink local) et les expose derrière **un port unique**. Un client qui contacte
ce port est servi par **un membre éligible** du pool : concrètement, le pool
sélectionne un proxy dont le dongle n'est pas en reset, puis relaie la connexion
vers lui.

Le pool est un point d'entrée, pas un uplink : il ne crée jamais de route, il ne
touche ni à la table `main` ni aux tables dédiées. Le trafic continue de sortir
par le proxy 3proxy du dongle sélectionné, donc par l'interface de ce dongle.

## Modèle

- `proxy_pools` : `id`, `label`, `listen_port` (unique), `auth_user`,
  `password_encrypted` (Fernet, même coffre que les proxies), `enabled`,
  `created_at`, `updated_at`.
- `proxy_pool_members` : `(pool_id, proxy_id)` unique. Un proxy peut appartenir
  à plusieurs pools ; un pool peut contenir des proxies de dongles et des
  proxies d'uplink local.

Le mot de passe du pool est généré à la création (même alphabet que les proxies,
sans caractères ambigus), chiffré au repos, consultable uniquement par l'API
authentifiée (`GET /api/v1/pools/{id}/credentials`, `Cache-Control: no-store`,
action journalisée).

## Éligibilité d'un membre

Un membre est **éligible** si et seulement si :

| Condition | Raison du refus (statut API) |
| --- | --- |
| le proxy existe et `enabled` | `proxy désactivé` |
| `proxy.state == RUNNING` | `proxy non démarré (state=…)` |
| `proxy.network_ip` connu | `adresse interne inconnue` |
| mot de passe proxy déchiffrable | `identifiants non récupérables` |
| proxy dongle : dongle existant | `dongle absent` |
| proxy dongle : `state ∈ {ONLINE, DEGRADED}` | `dongle non connecté (state=…)` |
| proxy dongle : `state != RESETTING` **et** verrou de reset non tenu | `dongle en reset` |

Les proxies `LOCAL` (uplink hôte) sont éligibles dès qu'ils sont activés,
`RUNNING` et que leurs identifiants sont déchiffrables : ils n'ont pas de reset.

Un pool sans membre éligible **refuse** la connexion (`502`) au lieu de retomber
sur un membre en reset : aucune sortie n'est jamais improvisée.

## Sélection

Sélection **par connexion** (une connexion client = un membre, pour toute sa
durée) : le membre éligible qui a le moins de connexions en cours, puis le moins
récemment utilisé, puis l'ordre stable des `proxy_id`. Ce choix est fait dans le
gateway, en mémoire ; il n'est pas persisté.

Conséquence assumée : un client qui ouvre plusieurs connexions peut sortir par
plusieurs IP publiques. Pour une IP stable, utiliser un proxy de dongle
directement (ou un pool à un seul membre).

## Protocole du port de pool

Le port parle **HTTP proxy** (le même protocole que les proxies 3proxy du
produit) :

- `CONNECT host:port HTTP/1.1` (HTTPS) : tunnel bidirectionnel après un
  `200 Connection established`.
- requête HTTP en forme absolue (`GET http://… HTTP/1.1`) : la requête est
  relayée au membre avec `Connection: close` ; le client doit donc rouvrir une
  connexion par requête (pas de keep-alive sur du HTTP en clair). Le keep-alive
  des tunnels `CONNECT` n'est pas affecté.
- Authentification `Proxy-Authorization: Basic …` **obligatoire** avec les
  identifiants du pool. Absente ou invalide → `407 Proxy Authentication
  Required` avec `Proxy-Authenticate: Basic realm="broxy4g-pool"`.

SOCKS5 n'est pas exposé : les proxies 3proxy du produit sont HTTP uniquement, et
le pool ne fait que relayer vers eux.

Le gateway parle à chaque membre en HTTP sur son adresse interne
(`proxy.network_ip:3128`) avec les identifiants **du proxy**, jamais ceux du
pool. Cette adresse est joignable depuis l'hôte et sa source (`172.28.0.1`) est
déjà dans les sources autorisées par défaut des proxies ; le retour est livré
localement (table `local`, priorité 0) et n'est donc pas routé vers l'uplink du
dongle.

## Ports et exposition

- Plage dédiée : `31301-31399` (`POOL_PORT_START`/`POOL_PORT_END`), disjointe de
  la plage des proxies (`31201-31299`) et du port de l'API (`8080`).
- Le port est alloué à la création, persisté, et **ne change pas** (comme pour
  les proxies) : un redémarrage ne modifie pas les URLs des clients.
- Le gateway écoute sur la même interface d'écoute que les proxies
  (`proxy.listen_interface`) : `0.0.0.0` si aucune interface n'est configurée,
  l'IPv4 de l'interface sinon. Interface configurée absente ou sans IPv4 → le
  pool n'écoute pas et le statut l'indique.
- L'adresse à communiquer aux clients est `resolve_published_ip()` du service
  d'écoute, identique à celle des proxies.

## Cycle de vie

- Création : allocation de port, génération des identifiants, persistance, puis
  démarrage de l'écoute. La création est atomique du point de vue API : si
  l'écoute échoue, le pool existe quand même et son statut expose l'erreur (même
  logique que les proxies, dont l'état est réconcilié).
- Désactivation (`enabled=false`) : l'écoute est fermée, les connexions en
  cours ne sont pas coupées ; les membres restent attachés.
- Suppression : arrêt de l'écoute, suppression des membres puis du pool.
- Démarrage de l'application : tous les pools activés sont réécoutés.
- Réconciliation (worker) : à chaque cycle, l'écoute est réalignée sur les pools
  activés (démarrage manquant, arrêt d'un pool désactivé, redémarrage si l'IP de
  bind a changé).

## API

| Méthode | Route | Rôle |
| --- | --- | --- |
| GET | `/api/v1/pools` | liste (membres, écoute) |
| POST | `/api/v1/pools` | création (`label`), renvoie le mot de passe une fois |
| GET | `/api/v1/pools/{id}` | détail |
| PATCH | `/api/v1/pools/{id}` | `label`, `enabled` |
| DELETE | `/api/v1/pools/{id}` | suppression |
| POST | `/api/v1/pools/{id}/members` | ajout (`proxy_id`) |
| DELETE | `/api/v1/pools/{id}/members/{proxy_id}` | retrait |
| GET | `/api/v1/pools/{id}/status` | écoute + membres éligibles/exclus avec raison |
| GET | `/api/v1/pools/{id}/credentials` | identifiants du pool (no-store, journalisé) |

Toutes les routes sont derrière l'authentification de l'API.

## Sécurité

- Aucun pool activé sans identifiants : le pool n'est jamais un proxy ouvert.
- Les identifiants des membres ne sont jamais exposés par les routes de pool ;
  seul le couple du pool l'est, et les erreurs d'authentification ne distinguent
  pas « utilisateur inconnu » de « mot de passe faux ».
- Le pool ne peut pas sortir autrement que par les proxies membres : il ne fait
  que relayer, et les règles nftables par proxy restent la seule autorité
  d'egress. Un pool vide ne produit aucun trafic.
- Toute action d'administration (création, suppression, activation, consultation
  des identifiants, ajout/retrait de membre) est journalisée.

## Limites connues

- Pas d'affinité de session : voir « Sélection ».
- Pas de SOCKS5.
- Pas de comptabilité fine par pool (débit, quotas) : le suivi reste celui des
  proxies.
- Une connexion en cours n'est pas coupée quand son dongle passe en reset ; le
  reset ferme le conteneur, donc la connexion tombe d'elle-même.
