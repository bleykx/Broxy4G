# syntax=docker/dockerfile:1

# Stage 1: build the frontend (multi-arch base image)
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
# Dependencies first: this layer stays cached until package.json /
# package-lock.json change, so `npm ci` is not re-run on every source edit
# (~2-3 min on a Raspberry Pi).
COPY frontend/package*.json ./
# --include=dev is explicit: the build needs devDependencies (tsc, vite).
# --no-audit --no-fund cut the number of registry round-trips.
# NODE_OPTIONS disables Node 20's "happy eyeballs" (autoSelectFamily) path: the
# registry publishes AAAA records, and in a container without a usable IPv6
# route that path can make `npm ci` drain its event loop and exit **0** with a
# half-installed node_modules (npm/cli#6409) — a silent failure that only shows
# up later as `sh: tsc: not found`. Cheap insurance, not the cause of a broken
# build: a container with no working DNS fails before npm ever gets that far.
# The `test -x` assertions fail fast, at this step and with a readable message,
# instead of a late and obscure failure in the build step below.
RUN NODE_OPTIONS=--no-network-family-autoselection npm ci --include=dev --no-audit --no-fund \
 && test -x node_modules/.bin/tsc \
 && test -x node_modules/.bin/vite
COPY frontend/ ./
RUN npm run build

# Stage 2: Docker CLI + compose plugin, from the official multi-arch image
# (no x86-only binary download, no third-party apt repo). The app calls the
# `docker compose` plugin to manage 3proxy containers, so both must ship here.
FROM docker:29-cli AS dockercli

# Stage 3: Python runtime (multi-arch base image)
FROM python:3.11-slim AS runtime

# modemmanager: provides mmcli (D-Bus client for the host's ModemManager)
# nftables/iproute2: network isolation and per-dongle routing
# curl: healthcheck and connectivity checks
# iputils-ping: ICMP probe of the connectivity worker (curl TCP fallback
#               already exists in the adapter if ping is unavailable)
# adb: Android Debug Bridge binary, needed for smartphone dongles (transport
#      `adb`); its path is made explicit via BROXY4G_ADB_PATH below
# (NetworkManager is NOT installed: nmcli is not used inside the container)
RUN apt-get update && apt-get install -y --no-install-recommends \
    modemmanager nftables iproute2 curl ca-certificates iputils-ping adb \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI and its plugins (docker-compose, docker-buildx)
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/

WORKDIR /opt/broxy4g

# Order matters: pyproject.toml first, then src/ (pip install . requires the
# package tree to exist, see [tool.setuptools.packages.find] where = ["src"])
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir .

# Frontend built in stage 1
COPY --from=frontend /app/frontend/dist/ /opt/broxy4g/frontend/dist

ENV PYTHONUNBUFFERED=1 \
    BROXY4G_FRONTEND_DIST=/opt/broxy4g/frontend/dist \
    BROXY4G_DB_PATH=/var/lib/broxy4g/broxy4g.db \
    BROXY4G_ADB_PATH=/usr/bin/adb \
    HOME=/var/lib/broxy4g

EXPOSE 8080

# /health is public (no auth) and the right liveness target
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/health || exit 1

CMD ["python", "-m", "broxy4g.main"]
