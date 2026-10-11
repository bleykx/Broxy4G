# Broxy4G Deployment

## Prerequisites

Broxy4G requires the following software to be installed on the host system:

- Linux operating system (Ubuntu 20.04 or newer recommended)
- ModemManager (for USB modem management)
- NetworkManager (for network interface management)
- Docker (for proxy container management)
- nftables (for network isolation)
- iproute2 (for routing management)
- curl (for connectivity checks)

## One-shot Install (recommended)

On a fresh machine you can run the single idempotent install script from the
repository root:

```bash
sudo deploy/install.sh
```

The script is safe to re-run for upgrades. It performs all manual steps
documented below: system user, directories, PolicyKit rule, systemd service,
env file, Python venv, frontend build, and service start.

> **Tip:** If `frontend/dist` is not yet built, either build it first
> (`cd frontend && npm ci && npm run build`) or have Node.js installed so the
> script can do it automatically.

## System Setup

### Create System User

```bash
sudo useradd --system --user-group --no-create-home --shell /bin/false broxy4g
```

### Create Directories

```bash
sudo mkdir -p /var/lib/broxy4g
sudo mkdir -p /etc/broxy4g
sudo chown -R broxy4g:broxy4g /var/lib/broxy4g
```

### ModemManager PolicyKit Rule

The service runs as the unprivileged user `broxy4g`. ModemManager requires
authorization for the `org.freedesktop.ModemManager1.Device.Control` action
(connect, disconnect, configure modems). By default this action is reserved
for administrators, so the service must be explicitly authorized.

Create this rule on **every machine** where the service runs:

```bash
sudo tee /etc/polkit-1/rules.d/50-broxy4g.rules <<'EOF'
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.ModemManager1.Device.Control" &&
        subject.user == "broxy4g") {
        return polkit.Result.YES;
    }
});
EOF
```

No restart of polkitd or the service is required: rules in `rules.d` are
picked up automatically. Without this rule, `mmcli --simple-connect` fails
with `PolicyKit authorization failed` and the dongle cannot be connected.

## systemd Setup

### Copy Service File

```bash
sudo cp deploy/broxy4g.service /etc/systemd/system/broxy4g.service
```

### Copy Environment File

```bash
sudo cp deploy/broxy4g.env.example /etc/broxy4g/broxy4g.env
```

### Enable and Start Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable broxy4g
sudo systemctl start broxy4g
```

## Docker Setup

### Prerequisites

- Docker must be **installed and running on the host**: Broxy4G manages the
  per-dongle 3proxy containers through the host Docker daemon
  (`/var/run/docker.sock` is mounted into the container). Without it, no proxy
  can ever be started.
- The host directory must exist before starting the container:

```bash
sudo mkdir -p /var/lib/broxy4g
```

- The API password is mandatory and must be provided in the environment
  (never hardcode it in `docker-compose.yml`):

```bash
export BROXY4G_API_PASSWORD='a-long-random-password'
```

### Using docker compose

```bash
# Build and start the service
docker compose build
docker compose up -d

# View logs
docker compose logs -f
```

The container runs `network_mode: host` (nftables, `ip rule` and the per-dongle
routing tables must target the host network namespace). It therefore binds
`0.0.0.0:8080` directly on the host; no `ports:` mapping exists or is needed.

### Manual Docker Run

```bash
docker run -d \
  --name broxy4g \
  --privileged \
  --network host \
  -v /var/lib/broxy4g:/var/lib/broxy4g \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /run/dbus:/run/dbus \
  -e BROXY4G_API_USER=admin \
  -e BROXY4G_API_PASSWORD="$BROXY4G_API_PASSWORD" \
  -e BROXY4G_DB_PATH=/var/lib/broxy4g/broxy4g.db \
  -e BROXY4G_DATA_DIR=/var/lib/broxy4g/proxies \
  -e BROXY4G_FRONTEND_DIST=/opt/broxy4g/frontend/dist \
  -e BROXY4G_HOST=0.0.0.0 \
  -e BROXY4G_PORT=8080 \
  -e HOME=/var/lib/broxy4g \
  --restart unless-stopped \
  broxy4g:local
```

### ARM64 / Raspberry Pi

- Raspberry Pi OS **64 bits** is required (arm64/aarch64); 32-bit systems
  (armv7l) are not supported because the Python wheels are not available for
  them.
- Every base image (`python:3.11-slim`, `node:20-alpine`, `docker:29-cli`,
  `3proxy/3proxy`) is multi-arch, so `docker compose build` works natively on
  the Pi with no architecture overrides.
- To cross-build an arm64 image from a non-ARM machine:

```bash
docker buildx build --platform linux/arm64 -t broxy4g:local .
```

- `network_mode: host` is mandatory on ARM as well: nftables rules and
  `ip rule`/`ip route` must be applied inside the host network namespace.

## Environment Variables

| Variable | Description | Default |
|------------|---------------|---------|
| BROXY4G_API_USER | Basic auth username for the API | admin |
| BROXY4G_API_PASSWORD | Basic auth password for the API (mandatory, empty value is a startup error) | none (required) |
| BROXY4G_DB_PATH | Database file path | /var/lib/broxy4g/broxy4g.db |
| BROXY4G_DATA_DIR | Base directory for per-proxy config files | `<dir of BROXY4G_DB_PATH>/proxies` |
| BROXY4G_FRONTEND_DIST | Path of the built frontend to serve | /opt/broxy4g/frontend/dist (Docker) |
| BROXY4G_PROXY_HOST_IPS | Comma-separated host IPs to route through proxies | none |
| BROXY4G_HOST | Host to bind to | 0.0.0.0 |
| BROXY4G_PORT | Port to listen on | 8080 |

## Security Notes

### API Security

- The API requires HTTP Basic authentication (`BROXY4G_API_USER` /
  `BROXY4G_API_PASSWORD`)
- `BROXY4G_API_PASSWORD` must be set to a strong value (an empty password
  makes the API fail at startup)
- API endpoints should not be exposed to untrusted networks

### Capabilities and Permissions

The systemd service is configured with:
- `CAP_NET_ADMIN` and `CAP_NET_RAW` for network management
- `SupplementaryGroups=docker` for Docker socket access
- `ProtectSystem=strict` and `ProtectHome=true` for security hardening
- `PrivateTmp=true` for an isolated, writable private `/tmp`
- `Environment=HOME=/var/lib/broxy4g` (required, see below)

### HOME under the systemd sandbox

With `ProtectHome=true`, anything under `/home` is not readable by the
service. `HOME` must therefore point to a readable directory, otherwise
`docker compose` fails at startup (rc=125, `unknown shorthand flag: 'p' in
-p`) because it cannot load its config and plugins. The service file sets
`HOME=/var/lib/broxy4g`, which is already covered by `ReadWritePaths` and
owned by the `broxy4g` user.

### Orphaned proxies

If a proxy row survives the removal of its dongle (for example because the
Docker cleanup failed), the reconciliation worker detects it on every run
and deletes it. The provisioning service also clears a stale proxy sharing
the same `source_ref` (IMEI) before creating a new one, so re-enrolling the
same dongle never fails on the `UNIQUE(source_type, source_ref)` constraint.

### Temporary Files

`ProtectSystem=strict` makes the whole filesystem read-only except
`ReadWritePaths`. The system temporary directories (`/tmp`, `/var/tmp`,
`/usr/tmp`) are therefore **not** writable by the service. Broxy4G never relies
on them: transient files (for example the nftables ruleset applied via
`nft -f`) are written to the inscriptible state directory derived from
`BROXY4G_DB_PATH` (`/var/lib/broxy4g` by default, already covered by
`ReadWritePaths`). Ensure that directory exists and is owned by the `broxy4g`
user (the install script and the "Create Directories" step above take care of
this).

### No Secrets in Logs

- No secrets or sensitive information should be logged
- All sensitive data is handled in memory and never persisted to logs

### Network Isolation

- Each proxy is isolated to its specific dongle
- Traffic cannot cross between proxies
- No cross-uplink communication is possible