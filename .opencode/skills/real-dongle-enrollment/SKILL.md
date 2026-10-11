---
name: real-dongle-enrollment
description: Use ONLY when testing the enrollment of a real (physical) 4G/5G dongle on a Broxy4G host — delete the dongle if it is already enrolled, re-enroll it, and surface the failure detail. Triggers: "tester l'enrôlement réel", "enrôler un dongle réel", "ré-enrôler un dongle", "re-enroll real dongle", "dongle bloqué en ERROR", "voir last_error", "pourquoi le dongle ne se connecte pas".
---

# Test de l'enrôlement réel d'un dongle

Procédure pour tester l'enrôlement d'un **dongle physique** : on supprime le
dongle s'il est déjà enrolé, on le ré-enrôle, on attend la reconciliation, puis
on affiche l'état final et, en cas d'échec, le détail `last_error`.

## Quand l'utiliser

- L'utilisateur veut valider l'enrôlement d'un dongle branché physiquement.
- Un dongle reste bloqué en `ERROR` et on veut connaître la cause exacte.
- Il faut « ré-enrôler » un dongle déjà présent (réinitialiser son enrôlement).

## Prérequis

- Dongle branché, ModemManager actif (`systemctl status ModemManager`).
- Service Broxy4G démarré (`systemctl status broxy4g`) ou instance locale
  (`python -m broxy4g.main`) avec un accès réel au matériel et à Docker.
- `curl` et `jq` installés.
- Auth HTTP Basic : l'API exige `BROXY4G_API_USER` / `BROXY4G_API_PASSWORD`
  (par défaut `admin` / valeur de l'env).

> Après toute modification du code backend, redémarrer le service
> (`sudo systemctl restart broxy4g`) et reconstruire le frontend si nécessaire
> (`cd frontend && npm run build`). La colonne `last_error` est ajoutée
> automatiquement à la base au démarrage (`init_db`).

## Variables

```bash
export BROXY4G_API_USER=admin
export BROXY4G_API_PASSWORD=change-me
export BROXY4G_API_BASE="http://127.0.0.1:${BROXY4G_PORT:-8080}"
```

## Procédure automatisée (recommandée)

Le script ci-dessous : (1) résout l'IMEI cible (argument ou premier dongle
découvert), (2) supprime le dongle s'il est déjà enrolé, (3) le ré-enrôle,
(4) attend la reconciliation (~30 s) et affiche `last_error` en cas d'échec.

```bash
#!/usr/bin/env bash
set -euo pipefail

BASE="${BROXY4G_API_BASE:-http://127.0.0.1:${BROXY4G_PORT:-8080}}"
USER="${BROXY4G_API_USER:-admin}"
PASS="${BROXY4G_API_PASSWORD:?définir BROXY4G_API_PASSWORD}"
AUTH=(-u "$USER:$PASS")

IMEI="${1:-}"
APN="${2:-internet}"

api() { curl -sS "${AUTH[@]}" -H 'Content-Type: application/json' "$@"; }

# 1. Identité cible
if [[ -z "$IMEI" ]]; then
  IMEI=$(api "$BASE/api/v1/dongles/discovered" | jq -r '.[0].identity_imei // empty')
  [[ -n "$IMEI" ]] || { echo "Aucun dongle découvert. Branchez-le, attendez ~10s, ou passez l'IMEI en argument."; exit 1; }
fi
echo "IMEI cible: $IMEI"

# 2. Supprimer s'il est déjà enrolé (match sur l'IMEI via le détail)
existing_id=$(api "$BASE/api/v1/dongles" | jq -r '.[].id' | while read -r id; do
  detail=$(api "$BASE/api/v1/dongles/$id")
  if [[ "$(jq -r '.identity_imei // empty' <<<"$detail")" == "$IMEI" ]]; then echo "$id"; break; fi
done)
if [[ -n "${existing_id:-}" ]]; then
  echo "Déjà enrolé ($existing_id) → suppression"
  api -X DELETE "$BASE/api/v1/dongles/$existing_id" >/dev/null
  sleep 3
fi

# 3. Récupérer l'identité découverte (serial USB / ICCID si présents)
disc=$(api "$BASE/api/v1/dongles/discovered" | jq -c ".[] | select(.identity_imei==\"$IMEI\")")
if [[ -z "$disc" ]]; then
  echo "Dongle pas encore découvert (cycle discovery ~10s). Réessayez dans quelques secondes."; exit 1
fi
USB=$(jq -r '.identity_usb_serial // ""' <<<"$disc")
ICCID=$(jq -r '.identity_iccid // ""' <<<"$disc")
LABEL=$(jq -r '.suggested_label // "Dongle"' <<<"$disc")

# 4. Enrôler
body=$(jq -n --arg i "$IMEI" --arg u "$USB" --arg c "$ICCID" --arg l "$LABEL" --arg a "$APN" \
  '{identity_imei:$i,
    identity_usb_serial:(if $u=="" then null else $u end),
    identity_iccid:(if $c=="" then null else $c end),
    label:$l, apn:$a}')
created=$(api -X POST "$BASE/api/v1/dongles" -d "$body")
id=$(jq -r '.id' <<<"$created")
echo "Enrôlé: $id (état initial: $(jq -r '.state' <<<"$created"))"

# 5. Attendre la reconciliation (boucle de 30s côté worker)
echo "Attente de la connexion réelle (reconciliation ~30s)..."
for _ in $(seq 1 12); do
  sleep 10
  d=$(api "$BASE/api/v1/dongles/$id")
  st=$(jq -r '.state' <<<"$d")
  echo "  état=$st"
  if [[ "$st" == "ONLINE" || "$st" == "ERROR" ]]; then
    if [[ "$st" == "ERROR" ]]; then
      echo "ÉCHEC — last_error:"; jq -r '.last_error // "(aucun détail)"' <<<"$d"; exit 2
    fi
    echo "SUCCÈS — IP publique: $(jq -r '.metrics.public_ip // "?"' <<<"$d")"; exit 0
  fi
done
echo "Timeout — état final: $(jq -r '.state' <<<"$d")"
jq -r '.last_error // "(aucun détail)"' <<<"$d"
exit 3
```

Usage :

```bash
BROXY4G_API_PASSWORD=change-me ./enroll.sh                 # auto-détecte le dongle
BROXY4G_API_PASSWORD=change-me ./enroll.sh 123456789012345 internet
```

## Procédure manuelle (curl)

```bash
# 1. Dongles détectés mais non enrolés → récupérer l'IMEI
curl -su "$BROXY4G_API_USER:$BROXY4G_API_PASSWORD" "$BASE/api/v1/dongles/discovered" | jq

# 2. Dongles déjà enrolés (supprimer celui visé)
curl -su "$BROXY4G_API_USER:$BROXY4G_API_PASSWORD" "$BASE/api/v1/dongles" | jq '.[].id'
curl -su "$BROXY4G_API_USER:$BROXY4G_API_PASSWORD" -X DELETE "$BASE/api/v1/dongles/<id>"

# 3. Enrôler
curl -su "$BROXY4G_API_USER:$BROXY4G_API_PASSWORD" -H 'Content-Type: application/json' \
  -X POST "$BASE/api/v1/dongles" \
  -d '{"identity_imei":"123456789012345","identity_usb_serial":null,"identity_iccid":null,"label":"Dongle 2345","apn":"internet"}' | jq

# 4. Suivre l'état et le détail d'erreur
curl -su "$BROXY4G_API_USER:$BROXY4G_API_PASSWORD" "$BASE/api/v1/dongles/<id>" | jq '.state, .last_error'
```

Interface web : la page détail du dongle
(`http://<host>:8080/#/dongle/<id>`) affiche un panneau rouge
« Connection failed » avec le contenu de `last_error` quand l'état est `ERROR`.

## Interpréter le résultat

- `state == "ONLINE"` → enrôlement + connexion réels réussis.
- `state == "ERROR"` → le champ `last_error` contient la cause
  (`NomException: message`, ou `modem introuvable (...)`, incluant APN,
  interface et sortie d'erreur de la commande système fautive).
- L'enrôlement passe d'abord par `CONFIGURING` : c'est le worker de
  reconciliation (toutes les 30 s) qui déclenche la connexion réelle.

## Reset d'un dongle

```bash
curl -su "$BROXY4G_API_USER:$BROXY4G_API_PASSWORD" -X POST "$BASE/api/v1/dongles/<id>/reset" | jq
```

Réponse : `{"status": "DONE"|"ERROR"|"PENDING", "message": <détail|null>}`.
`PENDING` = reset déjà en cours ; en `ERROR`, `message` porte le détail.

## Dépannage

- Aucun dongle découvert : `mmcli -L` doit lister le modem ; vérifier
  ModemManager et le câble.
- `last_error` vide alors que l'état est `ERROR` : consulter les logs
  `journalctl -u broxy4g -f` (l'exception y est tracée).
- `mmcli ... PolicyKit authorization failed` : la règle PolicyKit de
  `docs/deployment.md` n'est pas installée pour l'utilisateur `broxy4g`.
- 409 « already enrolled » au POST : supprimer d'abord le dongle existant
  (étape 2).
