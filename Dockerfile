# syntax=docker/dockerfile:1

# Stage 1: build the frontend (multi-arch base image)
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
# Copy the sources BEFORE installing: `npm ci` wipes and rebuilds node_modules
# from the lockfile, so nothing coming from the build context can clobber the
# freshly installed dependencies. This matters because tsc/vite live in
# devDependencies, and a stale host node_modules (whose .bin/tsc may be a
# dangling link) must never be able to shadow them.
COPY frontend/ ./
# --include=dev is explicit: the build needs devDependencies (tsc, vite).
# NODE_OPTIONS disables Node 20's "happy eyeballs" (autoSelectFamily) path.
# On a host whose IPv6 route is blackholed/flaky (this Raspberry Pi, where DNS
# resolution is already unstable) the buggy socket handling lets `npm ci` drain
# its event loop and exit 0 with a HALF-INSTALLED node_modules (npm/cli#6409),
# which then only shows up as a misleading `sh: tsc: not found` in the next step.
# `--no-audit --no-fund` also cut the number of registry round-trips.
# The `test -x` assertions fail fast, at this step and with a readable message,
# instead of a late and obscure failure in the build step below.
RUN NODE_OPTIONS=--no-network-family-autoselection npm ci --include=dev --no-audit --no-fund \
 && test -x node_modules/.bin/tsc \
 && test -x node_modules/.bin/vite
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
# (NetworkManager is NOT installed: nmcli is not used inside the container)
RUN apt-get update && apt-get install -y --no-install-recommends \
    modemmanager nftables iproute2 curl ca-certificates iputils-ping \
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
    HOME=/var/lib/broxy4g

EXPOSE 8080

# /health is public (no auth) and the right liveness target
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/health || exit 1

CMD ["python", "-m", "broxy4g.main"]
