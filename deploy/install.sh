#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# deploy/install.sh — Idempotent root installation of Broxy4G.
# Safe to re-run for upgrades.
# ------------------------------------------------------------------------------
set -euo pipefail

# --------------------------------------------------------------------------- helpers
log()  { printf '[install] %s\n' "$*"; }
warn() { printf '[install] WARNING: %s\n' "$*" >&2; }

# 1. Root check
if [[ $EUID -ne 0 ]]; then
    echo "[install] ERROR: this script must be run as root (sudo)" >&2
    exit 1
fi

# 2. Resolve directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 3. Python ≥ 3.11
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
    || { echo "[install] ERROR: Python ≥ 3.11 required" >&2; exit 1; }
log "Python $(python3 --version | awk '{print $2}') OK"

# 4. System packages
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# libglib2.0-bin: gdbus, used for NetworkManager profile writes (raw D-Bus)
apt-get install -y --no-install-recommends \
    modemmanager network-manager libglib2.0-bin nftables iproute2 curl \
    python3-venv python3-pip ca-certificates

# 5. System user
if ! id -u broxy4g &>/dev/null; then
    useradd --system --user-group --no-create-home --shell /bin/false broxy4g
    log "Created system user broxy4g"
else
    log "User broxy4g already exists"
fi

# 6. Dirs
mkdir -p /var/lib/broxy4g /etc/broxy4g
chown -R broxy4g:broxy4g /var/lib/broxy4g
log "Directories ready"

# 7. PolicyKit rule
cat > /etc/polkit-1/rules.d/50-broxy4g.rules <<'POLKIT'
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.ModemManager1.Device.Control" &&
        subject.user == "broxy4g") {
        return polkit.Result.YES;
    }
});
POLKIT
log "PolicyKit rule installed"

# 8. udev rules (Android USB access for the service user)
UDEV_RULES_SRC="$REPO_ROOT/deploy/99-broxy4g-adb.rules"
UDEV_RULES_DST="/etc/udev/rules.d/99-broxy4g-adb.rules"
if [[ -f "$UDEV_RULES_DST" ]] && cmp -s "$UDEV_RULES_SRC" "$UDEV_RULES_DST"; then
    log "udev rules up to date"
else
    install -m 0644 "$UDEV_RULES_SRC" "$UDEV_RULES_DST"
    udevadm control --reload-rules
    udevadm trigger
    log "udev rules installed (Android USB access for group broxy4g)"
fi

# 8b. NetworkManager hardening: never auto-create a default connection for a
# profile-less device (isolation leak during dongle profile rewrites)
NM_CONF_SRC="$REPO_ROOT/deploy/98-broxy4g-no-auto-default.conf"
NM_CONF_DST="/etc/NetworkManager/conf.d/98-broxy4g-no-auto-default.conf"
if [[ -f "$NM_CONF_DST" ]] && cmp -s "$NM_CONF_SRC" "$NM_CONF_DST"; then
    log "NM hardening up to date"
else
    mkdir -p /etc/NetworkManager/conf.d
    install -m 0644 "$NM_CONF_SRC" "$NM_CONF_DST"
    systemctl restart NetworkManager
    log "NM hardening installed (no-auto-default=*)"
fi

# 8c. dhcpcd hardening (Raspberry Pi OS only: dhcpcd runs alongside NetworkManager).
# Without this, dhcpcd takes a SECOND DHCP lease on smartphone USB interfaces
# (enx*) and installs a default route in table main via the dongle — an
# isolation leak that the app's guard_default_route then deletes at every
# rebind. See docs/networking.md (section « dhcpcd »).
DHCPCD_CONF="/etc/dhcpcd.conf"
DHCPCD_DENY='denyinterfaces dongle* enx* wwan* wwx*'
if [[ ! -f "$DHCPCD_CONF" ]]; then
    log "dhcpcd not present (no $DHCPCD_CONF) — skipping dhcpcd hardening"
elif grep -qF "$DHCPCD_DENY" "$DHCPCD_CONF"; then
    log "dhcpcd denyinterfaces already in place"
else
    cp -a "$DHCPCD_CONF" "${DHCPCD_CONF}.broxy4g.bak"
    if grep -q '^[[:space:]]*denyinterfaces' "$DHCPCD_CONF"; then
        sed -i "0,/^[[:space:]]*denyinterfaces.*/s//${DHCPCD_DENY}/" "$DHCPCD_CONF"
    else
        sed -i "1i ${DHCPCD_DENY}" "$DHCPCD_CONF"
    fi
    log "dhcpcd denyinterfaces installed (backup: ${DHCPCD_CONF}.broxy4g.bak)"
    # SIGHUP the manager: dhcpcd re-reads its config and deconfigures the
    # interfaces it no longer manages. NEVER restart/stop dhcpcd: a restart
    # would deconfigure ALL managed interfaces, including the management uplink.
    DHCPCD_PID="$(systemctl show -p MainPID --value dhcpcd 2>/dev/null || echo 0)"
    if [[ "$DHCPCD_PID" =~ ^[0-9]+$ ]] && (( DHCPCD_PID > 0 )); then
        kill -HUP "$DHCPCD_PID"
        log "SIGHUP sent to dhcpcd (pid $DHCPCD_PID) — rogue lease/routes deconfigured"
    else
        warn "dhcpcd is not running — denyinterfaces applies at its next start"
    fi
fi

# 9. systemd service
cp "$REPO_ROOT/deploy/broxy4g.service" /etc/systemd/system/broxy4g.service
log "systemd service installed"

# 10. Env file (idempotent upgrade)
ENV_FILE="/etc/broxy4g/broxy4g.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$REPO_ROOT/deploy/broxy4g.env.example" "$ENV_FILE"
    chown broxy4g:broxy4g "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    log "Env file created from example"
    warn "CHANGE BROXY4G_API_PASSWORD before exposing the API!"
else
    log "Env file $ENV_FILE already exists — kept as-is"
fi
# Upgrade path: add BROXY4G_FRONTEND_DIST if missing
if ! grep -q '^BROXY4G_FRONTEND_DIST=' "$ENV_FILE"; then
    echo 'BROXY4G_FRONTEND_DIST=/opt/broxy4g/frontend/dist' >> "$ENV_FILE"
    log "Added BROXY4G_FRONTEND_DIST to $ENV_FILE"
fi

# 11. App install to /opt/broxy4g
APP_DIR="/opt/broxy4g"

# Stop service before replacing files (first install: unit may not exist yet)
systemctl stop broxy4g 2>/dev/null || true

# Clean previous install
rm -rf "$APP_DIR/src" "$APP_DIR/pyproject.toml" "$APP_DIR/frontend"

# Copy fresh source (exclude node_modules from frontend copy)
cp -a "$REPO_ROOT/src" "$APP_DIR/"
cp -a "$REPO_ROOT/pyproject.toml" "$APP_DIR/"
mkdir -p "$APP_DIR/frontend"
tar -C "$REPO_ROOT/frontend" --exclude=node_modules -cf - . \
    | tar -C "$APP_DIR/frontend" -xf -

# Frontend build — always rebuild when npm is available so upgrades are never stale
if command -v npm &>/dev/null; then
    log "Building frontend (npm ci + npm run build)…"
    (cd "$APP_DIR/frontend" && npm ci && npm run build)
elif [[ -d "$APP_DIR/frontend/dist" ]]; then
    warn "npm not found — using pre-built frontend/dist (may be stale)"
else
    echo "[install] ERROR: frontend/dist not found and npm is not installed."
    echo "         Install node.js or build frontend first, then re-run." >&2
    exit 1
fi
log "Frontend built/ready"

# Python venv (recreate if missing OR broken — e.g. pip shebang wired to another venv)
if [[ ! -x "$APP_DIR/.venv/bin/pip" ]] || \
   ! head -1 "$APP_DIR/.venv/bin/pip" 2>/dev/null | grep -q "$APP_DIR/.venv"; then
    rm -rf "$APP_DIR/.venv"
    python3 -m venv "$APP_DIR/.venv"
    log "Recreated Python venv"
fi
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet --no-cache-dir "$APP_DIR"
log "Python package installed (non-editable)"

# 12. Docker check
if ! command -v docker &>/dev/null; then
    warn "docker not found — proxy containers will not work"
fi

# 13. systemd
systemctl daemon-reload
systemctl enable broxy4g
systemctl restart broxy4g
log "Service enabled and restarted"

# 14. Summary + UI health check
STATUS="$(systemctl is-active broxy4g 2>/dev/null || echo unknown)"

# Parse host/port from env file (defaults 0.0.0.0 / 8080)
BROXY4G_HOST="$(grep '^BROXY4G_HOST=' "$ENV_FILE" | head -1 | cut -d= -f2- || echo 0.0.0.0)"
BROXY4G_PORT="$(grep '^BROXY4G_PORT=' "$ENV_FILE" | head -1 | cut -d= -f2- || echo 8080)"

echo ""
echo "=============================="
echo " Broxy4G install complete"
echo "=============================="
echo " Service : $STATUS"
echo " URL     : http://${BROXY4G_HOST}:${BROXY4G_PORT}"
echo " Config  : $ENV_FILE"
echo " DB      : /var/lib/broxy4g/broxy4g.db"
echo ""

sleep 2
if curl -sf -o /dev/null "http://127.0.0.1:${BROXY4G_PORT}/"; then
    echo " Web UI responds at http://127.0.0.1:${BROXY4G_PORT}/"
else
    warn "Web UI did not respond — check 'journalctl -u broxy4g' for errors"
fi

if [[ "$STATUS" != "active" ]]; then
    warn "Service is not active. Run 'sudo systemctl status broxy4g' for details."
fi
echo " Reminder: change BROXY4G_API_PASSWORD in $ENV_FILE"
