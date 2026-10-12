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

### NetworkManager: keep dongle interfaces unmanaged

Broxy4G manages the dongle's interface itself (ModemManager bearer +
`ip addr/rule/route`). NetworkManager must never claim it. Because the
application **never renames** the netdev, the interface keeps its kernel
name (`wwan0`, `wwx0`, … — and possibly new names after a modem reset or
kernel update): the `unmanaged-devices` pattern must therefore match the
**kernel names** `wwan*`/`wwx*`, not only historical `dongle-*` names.

Create this configuration on **every machine** where the service runs:

```bash
sudo tee /etc/NetworkManager/conf.d/10-broxy4g-unmanaged.conf <<'EOF'
[device]
# dongle netdevs are managed by ModemManager + Broxy4G, never by NetworkManager
unmanaged-devices=interface-name:wwan*;interface-name:wwx*;interface-name:dongle-*
EOF
sudo systemctl restart NetworkManager
```

Restarting NetworkManager (or a reboot) applies the pattern. If the pattern
is missing or too narrow, NetworkManager may take over the dongle interface
and break the dedicated routing table / bearer IP configuration.

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
- The host data directory must exist, and the API password must be provided in
  the environment (never hardcode it in `docker-compose.yml`):

```bash
sudo mkdir -p /var/lib/broxy4g
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

### Build fails with "Temporary failure resolving"

During `docker compose build`, the image build fails with:

```text
Temporary failure resolving 'deb.debian.org'
E: Unable to locate package modemmanager
```

**Cause:** the build container has no usable DNS — a host-side problem, not a
repository problem. On systemd-resolved hosts (Ubuntu, Raspberry Pi OS) the
container inherits the `127.0.0.53` stub, unreachable inside the container, so
`apt-get update` never reaches `deb.debian.org`.

Diagnose on the host:

```bash
getent hosts deb.debian.org
cat /etc/resolv.conf
docker run --rm alpine:3.20 nslookup deb.debian.org
```

Solutions, in order of preference:

1. Pin a working DNS server for the Docker daemon. Find the resolver your host
   actually uses (`resolvectl status`, or `nmcli dev show | grep DNS`) and put
   its IP in `/etc/docker/daemon.json`:

   ```json
   {"dns": ["192.168.1.1", "1.1.1.1"]}
   ```

   then `sudo systemctl restart docker`. This fixes `docker compose build`
   permanently. Note that `127.0.0.53` (systemd-resolved stub) is **not** usable
   here: inside a container it points to the container's own loopback.

2. Build once with the host network stack, using plain `docker build` (the
   explicit flag grants the privilege buildx requires):

   ```bash
   docker build --network=host -t broxy4g:local .
   docker compose up -d --no-build
   ```

   (`--no-build` prevents compose from retrying the failing build and reuses
   the image you just built.)

   Do **not** add `build.network: host` to `docker-compose.yml` to achieve this:
   `docker compose build` would then fail with
   `additional privileges requested: pass "--allow=network.host"`, because
   buildx/Bake requires an explicit opt-in that needs host-side builder setup
   (`docker buildx create --allow-insecure-entitlement network.host`).

3. If the host itself has no working DNS/Internet access, build the image on
   another machine and transfer it:

   ```bash
   docker save broxy4g:local | ssh <pi> docker load
   ```

> **Warning:** also check for residual nftables rulesets from a previous
> Broxy4G run: a leftover rule can break Docker's internal forwarding/DNS
> (`sudo nft list ruleset`, and `sudo systemctl restart docker` if needed).

### Manual Docker Run

`docker-compose.yml` is the source of truth; the equivalent one-off command is:

```bash
docker run -d --name broxy4g --privileged --network host \
  -v /var/lib/broxy4g:/var/lib/broxy4g \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /run/dbus:/run/dbus \
  -e BROXY4G_API_PASSWORD="$BROXY4G_API_PASSWORD" \
  -e BROXY4G_DB_PATH=/var/lib/broxy4g/broxy4g.db \
  -e BROXY4G_DATA_DIR=/var/lib/broxy4g/proxies \
  -e HOME=/var/lib/broxy4g \
  --restart unless-stopped broxy4g:local
```

(`BROXY4G_HOST`, `BROXY4G_PORT` and `BROXY4G_FRONTEND_DIST` already have the
right defaults baked into the image.)

### Soft USB Recovery — sysfs Write Prerequisite

The reconciliation's **soft recovery** (bounded USB re-enumeration of a
dongle stuck in `ERROR`/`OFFLINE` with a PPP bearer, see
`docs/architecture.md`) writes `0` then `1` into
`/sys/bus/usb/devices/<id>/authorized` (a `root:root` `0644` file), so the
process needs write access to that sysfs subtree.

- **Docker (default deployment): no compose change is required.** The
  service already runs `privileged: true`, which makes `/sys` mounted
  read-write inside the container, and the image sets no `USER` (the process
  runs as root): the `authorized` files are writable. This was verified when
  soft recovery was introduced; `docker-compose.yml` was intentionally left
  unchanged.
- If `privileged: true` is ever removed, add the minimal read-write bind of
  the single subtree the app writes (a plain bind is read-write by default —
  do **not** downgrade the rest of the container to a read-only `/sys`
  without this):

  ```yaml
  volumes:
    - /sys/bus/usb/devices:/sys/bus/usb/devices
  ```

- **Native systemd deployment (`deploy/broxy4g.service`): soft recovery is
  currently disabled by the sandbox.** `ProtectSystem=strict` mounts `/sys`
  read-only and the service runs as the unprivileged user `broxy4g`, so the
  `authorized` files are not writable. The feature degrades gracefully (the
  toggle is skipped, the attempt is logged, the reconciliation cycle is not
  broken), but a stuck PPP dongle still requires a full reset. Enabling it
  under systemd would require running the service as root (or an ACL on the
  sysfs files) plus `ReadWritePaths=/sys/bus/usb/devices` — deliberately not
  applied to the unit file.

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