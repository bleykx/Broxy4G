#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# deploy/docker-up.sh - Idempotent docker compose bring-up of Broxy4G.
#
# Why this exists: `docker compose` resolves ${BROXY4G_API_PASSWORD:?...} from
# its OWN inputs (shell environment, project-root `.env`, or --env-file). It
# never reads /etc/broxy4g/broxy4g.env, which belongs to the systemd unit
# (deploy/broxy4g.service, EnvironmentFile=). This wrapper creates the root
# `.env` (gitignored, 0600) so the two deployment modes cannot drift, then
# builds, starts and health-checks the container.
#
# Safe to re-run: an existing `.env` is never overwritten.
# ------------------------------------------------------------------------------
set -euo pipefail

log() { printf '[docker-up] %s\n' "$*"; }
die() { printf '[docker-up] ERROR: %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
SYSTEMD_ENV="/etc/broxy4g/broxy4g.env"
EXAMPLE_ENV="$SCRIPT_DIR/docker.env.example"

HEALTH_URL="http://127.0.0.1:8080/health"
HEALTH_TIMEOUT=60

compose() { docker compose --env-file "$ENV_FILE" "$@"; }

cd "$REPO_ROOT"
[[ -f docker-compose.yml ]] || die "docker-compose.yml not found in $REPO_ROOT"
command -v docker &>/dev/null \
    || die "docker not found - install Docker first (see docs/deployment.md)"
docker info &>/dev/null \
    || die "cannot talk to the Docker daemon (is it running? retry with sudo)"

# 1. Provide the password to compose: project-root `.env`, read by the CLI.
if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$SYSTEMD_ENV" ]]; then
        cp "$SYSTEMD_ENV" "$ENV_FILE"
        log "Created $ENV_FILE from $SYSTEMD_ENV"
    else
        cp "$EXAMPLE_ENV" "$ENV_FILE"
        log "Created $ENV_FILE from $EXAMPLE_ENV"
    fi
    chmod 600 "$ENV_FILE"
else
    log "$ENV_FILE already exists - kept as-is"
fi
[[ -r "$ENV_FILE" ]] || die "$ENV_FILE is not readable by $(id -un) - fix with: sudo chown $(id -u):$(id -g) $ENV_FILE"

# 2. Fail fast on the placeholder/empty password instead of a 500 at runtime.
password="$(sed -n 's/^BROXY4G_API_PASSWORD=//p' "$ENV_FILE" | head -1)"
[[ -n "$password" ]] || die "BROXY4G_API_PASSWORD is empty in $ENV_FILE"
[[ "$password" != "change-me" ]] \
    || die "BROXY4G_API_PASSWORD is still 'change-me' in $ENV_FILE - set a strong password, then re-run"

# 3. Build and start (idempotent: recreates only what changed).
log "Building and starting: docker compose up -d --build"
compose up -d --build

# 4. Wait for the public /health endpoint (no auth required).
log "Waiting for $HEALTH_URL (up to ${HEALTH_TIMEOUT}s)"
for _ in $(seq "$HEALTH_TIMEOUT"); do
    if curl -fsS -o /dev/null "$HEALTH_URL"; then
        log "Healthy - Broxy4G is up on http://127.0.0.1:8080"
        exit 0
    fi
    sleep 1
done

printf '\n[docker-up] ERROR: %s did not respond within %ss.\n' \
    "$HEALTH_URL" "$HEALTH_TIMEOUT" >&2
printf '[docker-up] Last 40 log lines:\n' >&2
compose logs --tail=40 >&2 || true
exit 1
