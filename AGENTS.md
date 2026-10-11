# Projet

Ce dépôt est un gestionnaire de dongles 4G/5G Linux.

Travail avec les venv python

Il doit :
- Enrôler des dongles avec une identité matérielle stable
- Restaurer automatiquement leur configuration après reboot ou unplug/plug
- Gérer la connexion mobile, les routes, les règles nftables et un proxy 3proxy Docker par dongle
- Exposer une API REST et une interface web
- Afficher état, qualité du signal, ping, IP publique et santé du proxy
- Permettre le reset d'un dongle avec les statuts métier exacts `DONE`, `ERROR` et `PENDING`

# Priorités

Ordre de priorité :
1. Isolation réseau stricte entre dongles et proxies
2. Reconciliation fiable après reboot, crash ou reconnexion USB
3. Idempotence de toutes les opérations système
4. Sécurité, observabilité et testabilité
5. Simplicité de maintenance et fichiers courts

Ne jamais supposer que `wwan0`, `/dev/ttyUSB0`, l'ordre USB ou l'index ModemManager est stable.
Identifier un dongle par une identité matérielle persistante : IMEI en priorité, puis numéro de série USB et/ou ICCID lorsque nécessaire.

# Règles de taille et modularité

- Ne pas créer de fichier source applicatif de plus de 300 lignes.
- À partir de 220 lignes, proposer ou effectuer une extraction cohérente avant d'ajouter de nouvelles responsabilités.
- Limite recommandée : 150 lignes pour un routeur API, 200 lignes pour un service, 150 lignes pour un composant frontend.
- Une fonction ne doit en général pas dépasser 40 lignes.
- Une classe ne doit avoir qu'une responsabilité principale.
- Ne jamais créer de fichier `utils.py`, `helpers.py`, `common.py`, `manager.py` ou `services.py` fourre-tout.
- Nommer les modules selon leur responsabilité métier explicite.
- Les exceptions : migrations générées, lockfiles, snapshots, configuration générée et vendored code.
- Avant de modifier un fichier de plus de 220 lignes, rechercher un découpage cohérent par domaine.

# Architecture obligatoire

Séparer strictement :
- `api/` : routes HTTP, validation et sérialisation seulement
- `domain/` : modèles, règles métier, machines à états
- `services/` : orchestration des cas d'usage
- `adapters/` : ModemManager, NetworkManager, Docker, systemd, nftables, shell et D-Bus
- `repositories/` : persistance uniquement
- `workers/` : tâches de fond, découverte et reconciliation
- `frontend/src/features/` : fonctionnalité UI par domaine

Les routes API ne doivent jamais appeler directement `subprocess`, Docker, `ip`, `nft`, `mmcli` ou `nmcli`.
Ces appels doivent passer par des adaptateurs testables.

# Réseau et sécurité

- Ne jamais modifier la route par défaut globale de l'hôte pour sélectionner un dongle.
- Utiliser une table de routage dédiée par dongle et des règles `ip rule` basées sur des marques firewall.
- Utiliser nftables, pas iptables, sauf contrainte explicitement documentée.
- Chaque proxy doit être explicitement lié à un dongle et ne doit jamais pouvoir sortir par un autre uplink.
- Toutes les opérations réseau doivent être idempotentes et disposer d'un mécanisme de reconciliation.
- Ne jamais exposer une API d'administration ou un proxy sans authentification et contrôle d'accès.
- Ne jamais stocker un PIN SIM, mot de passe, jeton ou secret en clair dans Git, les logs ou les réponses API.
- Journaliser toute action destructive ou d'administration : enrôlement, désactivation, reset, création et suppression de proxy.

# États métier

États possibles d'un dongle :
`DISCOVERED`, `UNENROLLED`, `CONFIGURING`, `CONNECTING`, `ONLINE`, `DEGRADED`, `RESETTING`, `OFFLINE`, `ERROR`.

Le reset est exclusif par dongle et une action de **récupération** : il est
autorisé depuis `ONLINE`, `DEGRADED`, `OFFLINE` et `ERROR` (il repasse le dongle
en `RESETTING` puis reconnecte). Il est interdit depuis `DISCOVERED`,
`UNENROLLED` et `CONFIGURING` (le reset a besoin d'un dongle déjà enrôlé) : on
retourne alors `ERROR` avec un message métier propre de la forme
`reset impossible dans l'état <STATE>`, jamais de jargon technique.

- Si un reset est déjà actif, retourner exactement le statut métier `PENDING`.
- En succès, retourner exactement `DONE`.
- En échec ou timeout, retourner exactement `ERROR`.
- Le reset est borné dans le temps (`reset_timeout`) et le verrou est libéré
  dans un bloc de nettoyage même en cas de timeout/annulation.
- Ne jamais lancer deux conteneurs proxy actifs pour le même dongle.

Interlock de réconciliation : la reconciliation ne saute la restauration qu'un
dongle `RESETTING` **si le verrou de reset est réellement tenu** (reset en
cours). Un `RESETTING` **sans verrou** est un état résiduel post-crash ou
post-reboot : la restauration est relancée. L'application de l'isolation
nftables est sérialisée (single-flight) pour éviter tout entrelacement de `nft -f`
sous reset batch.

# Reconciliation

Le système doit restaurer l'état désiré après :
- Démarrage de la machine
- Redémarrage du service
- Rebranchement d'un dongle
- Crash ou redémarrage d'un conteneur proxy
- Perte puis retour de la connexion cellulaire

La reconciliation doit :
1. Identifier le matériel
2. Charger sa configuration persistée
3. Restaurer la connexion mobile
4. Vérifier interface, IP, route et DNS
5. Recréer routes, règles et firewall
6. Réconcilier le conteneur 3proxy
7. Vérifier la connectivité et l'IP publique
8. Publier l'état final

# Tests et validation

- Toute règle métier doit être couverte par des tests unitaires.
- Toute commande système doit être encapsulée derrière une interface simulable.
- Ajouter des tests pour : dongle inconnu, interface renommée, reboot, unplug/plug, échec de connexion, timeout, reset concurrent et crash pendant reset.
- Après chaque modification, exécuter les tests et le linter pertinents.
- Ne pas prétendre qu'une commande système ou une configuration a été validée si elle n'a pas été exécutée.
- Documenter les prérequis, les privilèges Linux, les capacités Docker et les règles réseau dans `docs/`.

# Documentation obligatoire

Mettre à jour la documentation lorsqu'une modification change :
- Le schéma de données
- Une route API
- Les règles de routage ou nftables
- Le cycle de vie d'un dongle
- Le déploiement Docker/systemd
- Une variable d'environnement

Consulter `docs/architecture.md` et `docs/networking.md` avant toute modification d'architecture ou de routage.
