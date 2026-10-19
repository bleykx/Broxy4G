# Broxy4G API

## Authentication

All API endpoints require HTTP Basic Auth:

```
Authorization: Basic <base64(user:pass)>
```

The credentials are configured via the `BROXY4G_API_USER` (default: "admin") and `BROXY4G_API_PASSWORD` environment variables.

### Query string auth (opt-in)

For external tools that can only pass a single URL (GET-only, e.g. Ankabot),
credentials may be sent in the query string:

```
GET /api/v1/dongles?user=<user>&password=<password>
```

This is **disabled by default (fail-closed)** and only read when the
`BROXY4G_ALLOW_QUERY_AUTH` environment variable is set to a truthy value
(`1`, `true`, `yes` or `on`, case-insensitive). Rules:

- The `Authorization` header always takes precedence: when it is present
  (even malformed), the query string is never consulted and a `401` is
  returned if the header does not carry valid credentials.
- Both `user` and `password` query parameters must be present; otherwise the
  request is rejected with `401 Unauthorized`. Note that 401 responses carry
  **no** `WWW-Authenticate` header on purpose: that challenge would trigger the
  browser's native auth popup in the web UI; the caller always supplies the
  `Authorization: Basic ...` header itself.
- Credentials are never logged. The uvicorn access log (emitted before any
  FastAPI middleware and containing the full query string) is passed through
  a redacting formatter that replaces the value of sensitive
  key=value pairs (`user`, `password`, `token`, …) with `***`.

Because URLs can leak via proxies, browser history and logs, keep this option
off unless a GET-only tool genuinely needs it. The frontend always uses Basic
Auth.

## Endpoints

### Health Check

**GET** `/health`

Returns the health status of the application.

**Response**
```json
{
  "status": "healthy"
}
```

This endpoint is **public (no authentication)** on purpose: it is polled by the
deployment scripts (UI health check in `deploy/install.sh`) and by infrastructure
probes that have no credentials. It checks process liveness only and verifies
**nothing** about the caller.

### Session

**GET** `/api/v1/session`

Verifies that the caller's Basic Auth credentials are valid, without acting on
any resource. This is the endpoint the web UI uses to check a login: it returns
`200` only when the `Authorization` header carries the configured
`BROXY4G_API_USER` / `BROXY4G_API_PASSWORD` pair.

Unlike the public `/health` above, this endpoint is **authenticated**: a
request without credentials, or with a wrong user/password, is rejected with
`401 Unauthorized` — the response carries **no** `WWW-Authenticate` header
(deliberate choice, to avoid the browser's native auth popup in the UI; the
caller supplies `Authorization: Basic ...` explicitly). It must not be used as
a substitute for `/health`, and `/health` must never be used to verify
credentials (it would succeed with any caller).

**Response**
```json
{
  "authenticated": true
}
```

### Dongle Management

#### List Dongles

**GET** `/api/v1/dongles`

Returns a list of all registered dongles.

**Response**
```json
[
  {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "label": "Dongle 1234",
    "state": "ONLINE",
    "interface_name": "wwan0",
    "public_ip": "192.168.1.100",
    "signal_quality": 85,
    "transport": "modem"
  }
]
```

`transport` is `"modem"` (the default) or `"adb"` (an Android smartphone
tethered over USB — the same dongle resource, a different transport, cf.
`docs/architecture.md` → *Device Transports*).

#### Get Dongle Details

**GET** `/api/v1/dongles/{id}`

Returns details for a specific dongle.

**Response**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "label": "Dongle 1234",
  "state": "ONLINE",
  "identity_imei": "123456789012345",
  "identity_usb_serial": "ABC123",
  "identity_iccid": "12345678901234567890",
  "identity_adb_serial": null,
  "apn": "internet",
  "apn_auth": "user:password",
  "route_table_id": 100,
  "fwmark": 1000,
  "interface_name": "wwan0",
  "proxy_id": "123e4567-e89b-12d3-a456-426614174001",
  "last_error": null,
  "created_at": "2023-01-01T12:00:00Z",
  "updated_at": "2023-01-01T12:00:00Z",
  "metrics": {
    "signal_quality": {
      "percent": 48,
      "rsrp": -92,
      "rsrq": -11,
      "sinr": 9,
      "rssi": -63
    },
    "ping": {
      "host": "8.8.8.8",
      "packets_sent": 4,
      "packets_received": 4,
      "min_ms": 28.1,
      "avg_ms": 30.4,
      "max_ms": 33.7
    },
    "public_ip": "37.167.72.57",
    "proxy_health": "healthy",
    "proxy_health_reason": null
  },
  "transport": "modem"
}
```

- `identity_adb_serial` is the ADB serial of a smartphone dongle; `null` for
  a modem. `transport` is `"modem"` or `"adb"`.
- For an `adb` dongle the identity is `identity_adb_serial` (the other
  identity fields are `null`) and `apn` stays `null`: a phone manages its own
  APN, so no APN is resolved or stored.

> **Signal metrics:** `signal_quality.percent` is always provided by
> ModemManager. The detailed fields (`rsrp`, `rsrq`, `sinr`, `rssi`) are read
> from `mmcli --signal-get`; when the values are not yet available they are
> `null`. The first read for a modem may return `null` detailed fields and
> transparently enables ModemManager signal polling (`mmcli --signal-setup=30`,
> requires the PolicyKit `org.freedesktop.ModemManager1.Device.Control` rule
> installed by `deploy/install.sh`); values appear on the next poll. Modems that
> do not report detailed signal (e.g. non-LTE) keep `null` detailed fields.
>
> **Ping:** latency is measured with ICMP first. When the carrier blocks ICMP
> over the dongle interface (observed on some mobile APNs), the adapter
> transparently falls back to TCP-connect timing via `curl --interface`, so
> `avg_ms` still reflects the real uplink latency.
>
> **Error detail:** `last_error` holds the detail of the last failure (exception
> type and message, or the reason such as an absent modem). It is populated when
> the dongle is in state `ERROR` and reset to `null` after a successful connect.
>
> **Proxy health:** `metrics.proxy_health` is computed live on each GET and
> reflects whether the dongle's 3proxy is actually usable, not just its stored
> state. Values:
>
> - `healthy` — the proxy is enabled, its container is `running`, its port is
>   published and reachable in TCP. `proxy_health_reason` is `null`.
> - `unhealthy` — something is wrong and `proxy_health_reason` explains which:
>   the container is not running (`conteneur non actif (state=...)`), the port
>   is not published because the configured listen interface is absent or has
>   no IPv4 (`port non publié : interface d'écoute absente ou sans IPv4`), or
>   the published port is not reachable from the LAN
>   (`port publié non joignable depuis le LAN (<ip>:<port>)` — the probe
>   targets the published port on the address a LAN client would use).
> - `unknown` — no health check applies: the dongle has no proxy
>   (`aucun proxy`) or its proxy is disabled (`proxy désactivé`).
>
> This is what turns the former silent failure (container `running` but port
> not published because the listen interface vanished) into an explicit
> `unhealthy` with a reason.

#### Get Discovered Dongles

**GET** `/api/v1/dongles/discovered`

Returns a list of dongles that are detected but not yet enrolled.

**Response**
```json
[
  {
    "identity_imei": "123456789012345",
    "identity_usb_serial": "ABC123",
    "identity_iccid": "12345678901234567890",
    "interface_name": "wwan0",
    "suggested_label": "Dongle 345",
    "suggested_apn": "free",
    "transport": "modem",
    "identity_adb_serial": null,
    "model": null
  },
  {
    "identity_imei": null,
    "identity_usb_serial": null,
    "identity_iccid": null,
    "interface_name": null,
    "suggested_label": "Téléphone SM-A217F",
    "suggested_apn": null,
    "transport": "adb",
    "identity_adb_serial": "R5CT10ABCDE",
    "model": "SM-A217F"
  }
]
```

- `transport` is `"modem"` or `"adb"`. `identity_adb_serial` is the ADB
  serial of a discovered phone (`null` for a modem); `model` is the phone
  model (ADB `model` field, falling back to `ro.product.model`) and `null`
  for a modem.
- Phones are announced only while ADB reports them in the `device` state:
  `unauthorized` (authorization prompt not accepted on the phone) and
  `offline` phones are silently ignored and never appear in this list.
  `suggested_apn` is always `null` for a phone.

`suggested_apn` est résolu depuis la base MBPI (mobile-broadband-provider-info) à
partir de l'IMSI/SIM et de l'opérateur courant. Il vaut `null` si aucun APN
plausible n'est trouvé. Il ne remplace jamais un APN saisi explicitement.

#### Enroll Dongle

**POST** `/api/v1/dongles`

Enrolls a new dongle.

**Request**
```json
{
  "identity_imei": "123456789012345",
  "identity_usb_serial": "ABC123",
  "identity_iccid": "12345678901234567890",
  "label": "Dongle 1234",
  "apn": "internet",
  "transport": "modem",
  "identity_adb_serial": null
}
```

- `transport` is optional and defaults to `"modem"`; the only accepted values
  are `"modem"` and `"adb"` (case-insensitive).
- `identity_adb_serial` is the ADB serial of an Android smartphone. It is
  **required** when `transport` is `"adb"` and **forbidden** when `transport`
  is `"modem"`.
- At least one identifier is required: `identity_imei`,
  `identity_usb_serial`, `identity_iccid` or `identity_adb_serial`.
- An unknown `transport` value returns `422 Unprocessable Entity` with a
  readable business message
  (`transport inconnu : « <valeur> » (valeurs acceptées : modem, adb)`).

Enrolling a phone (`"transport": "adb"`, `"identity_adb_serial":
"<serial>"`, no other identifier, no `apn`) immediately triggers the USB
tethering activation: a failure of that activation does **not** fail the
enrollment — the response reflects the device's final state
(`state: "ERROR"` + `last_error` if the connection failed).

**Response**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "label": "Dongle 1234",
  "state": "CONFIGURING",
  "identity_imei": "123456789012345",
  "identity_usb_serial": "ABC123",
  "identity_iccid": "12345678901234567890",
  "identity_adb_serial": null,
  "apn": "internet",
  "apn_auth": null,
  "route_table_id": null,
  "fwmark": null,
  "interface_name": null,
  "proxy_id": null,
  "last_error": null,
  "created_at": "2023-01-01T12:00:00Z",
  "updated_at": "2023-01-01T12:00:00Z",
  "metrics": null,
  "transport": "modem",
  "proxy_credentials": {
    "username": "khmtpznwqrbv",
    "password": "nHkq2z4vXw8mPq6rTn3j"
  }
}
```

The response is a dedicated `DongleEnrollmentResponse` (the `DongleDetail` shape
plus one extra field):

- `proxy_credentials` (`{ "username", "password" }`) is filled **only if this
  enrollment call provisioned a brand new proxy** (auto-create enabled). The
  plaintext password is returned **once, in this response only** — it is never
  persisted, logged, or returned by any other endpoint.
- `proxy_credentials` is `null` when no new proxy was created: auto-create is
  disabled (`PUT /api/v1/settings/proxy-auto-create`), or a proxy already
  exists for the dongle.

The GET endpoints (`GET /api/v1/dongles/{id}`, `GET /api/v1/dongles/{id}/proxy`)
never include `proxy_credentials` nor a plaintext password; they expose only
`auth_user` (the proxy username) and the state.

#### Connect Dongle

**POST** `/api/v1/dongles/{id}/connect`

Connects a dongle to the network.

**Response**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "state": "CONNECTING"
}
```

#### Disconnect Dongle

**POST** `/api/v1/dongles/{id}/disconnect`

Disconnects a dongle from the network.

**Response**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "state": "OFFLINE"
}
```

#### Reset Dongle

**POST / GET** `/api/v1/dongles/{id}/reset`

Resets a dongle. This is an exclusive, bounded operation and is a **recovery**
action: it is allowed from `ONLINE`, `DEGRADED`, `OFFLINE` and `ERROR`. The
dongle is moved to `RESETTING` (it stays `RESETTING` until `connect()` moves it
to `CONNECTING`), then reconnected. The USB re-enumeration can take up to
~60 s (the modem reappears ~15-50 s after `mmcli --reset`), so the whole
operation is wrapped in a bounded timeout (`BROXY4G_RESET_TIMEOUT_SECONDS`,
default `300 s`); the per-dongle lock is always released on completion, timeout
or cancellation.

**The reset contract and its status codes (`DONE` / `ERROR` / `PENDING`) are
unchanged and apply identically to both transports** (`modem` and `adb`).
For a smartphone the recovery mechanism differs from a modem: instead of a USB
re-enumeration, the reset performs a bounded airplane-mode radio cycle
(double `enable`/`disable`) and waits for the phone to come back
(`adb wait-for-device`); no USB re-enumeration is ever performed on a phone.
`DONE` still means the public IP has been **verified** by the probe.

`DONE` is only returned **after the public IP has been verified**: once the
modem is reconnected, the reset waits for the public-IP probe (pinned to the
bearer's source IP) to reach verdict `OK`. The probe **verdict** is the gate —
not a change of IP value (see the CGNAT caveat below). Because the public IP
can lag the bearer by a few seconds, this is a **bounded wait loop** (default
15 attempts, 2 s apart). If the verdict never becomes `OK`, the reset returns
`ERROR` ("IP publique non vérifiée après reset (sonde échouée)") **without
changing the dongle state** (it stays `ONLINE`): marking the dongle `ERROR`
would trigger a destructive USB re-enumeration (usb recovery) on a link that
is actually working.

**Response**
```json
{
  "status": "DONE",
  "message": null
}
```

- `status` is one of `DONE`, `ERROR`, `PENDING`.
- `message` is `null` on success (`DONE`), a fixed explanation on `PENDING`
  (reset already in progress, or within the post-`DONE` grace window), and the
  failure detail when the status is `ERROR`.
- `PENDING` is returned with HTTP `200` (not `409`) in two situations:
  - a reset is already **in progress** for that dongle (per-dongle lock held):
    the request was accepted and the reset is being handled by the
    already-running one;
  - the request falls within the **post-`DONE` grace window** (default
    `15 s`, `BROXY4G_RESET_POST_DONE_SECONDS`): a reset just succeeded and the
    operator should not be disconnected before their new public IP propagates.
    See the *Post-`DONE` grace window* paragraph below.
- A `ERROR` with a clean business message (no technical jargon) is returned when
  the dongle is in a state from which a reset is not meaningful — `DISCOVERED`,
  `UNENROLLED` or `CONFIGURING` (the reset needs an already-enrolled dongle).
  The message has the exact form `reset impossible dans l'état <STATE>`.

**Anti-storm amortisation.** Because a reset forces a destructive USB
re-enumeration (~15–50 s outage), repeated resets of the same dongle are
amortised by a per-dongle guard (cooldown default `60 s`, cap default
`5 resets / 600 s` window — configurable, see the deployment docs). A reset
requested while a previous executed reset is younger than the cooldown returns
`PENDING` with a cooldown message; a reset beyond the per-window cap returns
`ERROR` with a message beginning with `reset refusé`. Refusals never touch the
hardware. The status set stays exactly `DONE` / `ERROR` / `PENDING` (the GET
`text/plain` body contract is unchanged).

**Post-`DONE` grace window.** A short grace window (default `15 s`,
`BROXY4G_RESET_POST_DONE_SECONDS`) follows every `DONE`: a new reset requested
for the same dongle inside the window returns `PENDING` without re-resetting
the modem, so the operator is not disconnected right after obtaining their new
public IP. The window is per-dongle, in-memory, and distinct from both the
per-dongle lock ("reset **in progress**", consumed by reconciliation and USB
recovery) and the anti-storm throttle ("rate limiting"). It is (re)started only
by a `DONE` — a refusal by the window never re-times it. The order of checks is
throttle → grace window → lock.

**GET variant.** `GET /api/v1/dongles/{id}/reset` is exposed for external
GET-only tools (Ankabot) that can pass only a URL — combine it with the
opt-in query string auth to trigger a reset with a single link.
Behaviour is identical to the POST variant: same `ResetService`, same
per-dongle lock (a concurrent reset returns `PENDING`), same idempotence and
`DONE` / `ERROR` / `PENDING` statuses. Unlike the POST, which returns the
JSON `{"status": ..., "message": ...}` payload, the GET variant returns the
**raw business status** as a `text/plain` body: exactly `DONE`, `ERROR` or
`PENDING` — the exact body, with no trailing newline and HTTP `200`. This is
what a GET-only tool compares against (Ankabot expects the body `DONE`).
The GET response carries `Cache-Control: no-store` and `Pragma: no-cache` so
the outcome of an administrative action is never cached. The trigger is
logged (with the HTTP method used, without any secret).

**Modem reset.** The reset performs a real USB modem reset
(`mmcli -m <path> --reset`) after tearing down the bearer: this is the only
operation that renews the public IP (disconnect + connect, or even
`--delete-bearer`, keep it). The dongle is re-enumerated over USB — it
disappears from ModemManager for a while (typically 15–40 s on a Huawei E3372)
and comes back under a **new path**, possibly with a **renamed interface**; it
is found again by its hardware identity (IMEI). If the modem does not come
back within the bounded wait, the reset returns `ERROR`. On success, the
per-dongle metrics cache used by the dongle list (public IP, signal quality)
is refreshed immediately, so the dashboard no longer shows a stale IP.

**Proxy.** The dongle's proxy is paused for the duration of the reset and then
recreated. The 3proxy container is pinned to its dongle (dedicated routing table
+ firewall mark), so after a modem reset the previous bearer and routes are gone
and the container must be rebuilt to egress through the new session. The proxy's
*desired* state is preserved across the reset: a proxy the operator had disabled
stays disabled. If the reset fails or times out, the container is left stopped
but `enabled` stays `true`, so reconciliation restores the proxy once the dongle
has a usable uplink again — a proxy is never started on a dead uplink.

**CGNAT caveat.** An identical public IP before and after the reset does **not**
imply a failure: behind a carrier-grade NAT the operator may assign the same
public IP to two consecutive sessions. The USB modem reset guarantees a fresh
data session, so an unchanged *public IP* is a valid `DONE`. The `DONE` gate
therefore looks at the **probe verdict** (did the `curl`-based probe succeed?)
— not at a change of the IP value. A probe that succeeds with the same IP as
before is `OK` and yields `DONE`.

#### Reset Dongles (batch)

**POST** `/api/v1/dongles/reset`

Resets several dongles in parallel. Each dongle is reset independently: the
result of one dongle never affects the others.

**Request**
```json
{
  "ids": [
    "123e4567-e89b-12d3-a456-426614174000",
    "123e4567-e89b-12d3-a456-426614174001"
  ]
}
```

- `ids` is a non-empty list of dongle UUIDs. Duplicates are deduplicated while
  keeping the order of first appearance, so each dongle is reset at most once.

**Response**
```json
{
  "results": [
    {
      "dongle_id": "123e4567-e89b-12d3-a456-426614174000",
      "status": "DONE",
      "message": null
    },
    {
      "dongle_id": "123e4567-e89b-12d3-a456-426614174001",
      "status": "PENDING",
      "message": "reset déjà en cours"
    }
  ]
}
```

- `results` holds one entry per (unique) requested dongle, in the same order.
- Each `status` is one of `DONE`, `ERROR`, `PENDING` with the same meaning as
  the single-dongle reset (`PENDING` = a reset is already in progress for that
  dongle). `message` is `null` on `DONE`, a fixed explanation on `PENDING`, and
  the failure detail on `ERROR`.
- A dongle id that does not exist (or is not in a resettable state) yields its
  own `ERROR` entry; it does not affect the other entries of the batch.

Returns `422 Unprocessable Entity` if `ids` is empty, and `401 Unauthorized` if
not authenticated.

#### Update Dongle

**PATCH** `/api/v1/dongles/{id}`

Updates mutable fields of a dongle. Only the provided fields are changed.

**Request**
```json
{
  "label": "Dongle 1234",
  "apn": "internet",
  "apn_auth": "pap"
}
```

- `label`: new label for the dongle
- `apn`: new APN
- `apn_auth`: APN authentication method, one of `"none"`, `"pap"`, `"chap"`

**Response**: the updated dongle (same shape as `GET /api/v1/dongles/{id}`).

Returns `404 Not Found` if the dongle does not exist.

#### Delete Dongle

**DELETE** `/api/v1/dongles/{id}`

Deletes a dongle with a full teardown. Performs, in order:

1. Disconnect the modem and bring the interface down
2. Remove the dongle's routing rule (`ip rule`) and default route from its routing table
3. Remove the proxy container and its record if one exists
4. Delete the dongle record

All steps are idempotent; failures to disconnect or remove the proxy are logged and do not prevent the dongle record from being deleted.

**Response**

`204 No Content` (no body).

Returns `404 Not Found` if the dongle does not exist.

### Local Connections

Local connections expose host network interfaces (ethernet / wifi) as proxy
uplinks, alongside dongles. They are strictly pinned to the chosen interface
(dedicated routing table + firewall mark + nftables rules, cf.
`docs/networking.md`) and **cannot have their public IP reset** — unlike a
dongle, a host interface has no modem to re-enumerate.

#### List Local Connections

**GET** `/api/v1/local-connections`

Lists the host interfaces usable as proxy uplinks and whether each is enrolled.

**Response**
```json
[
  {
    "interface_name": "eth0",
    "ip": "192.168.1.10",
    "kind": "ethernet",
    "enrolled": false,
    "proxy_id": null,
    "gateway": "192.168.1.1",
    "link_state": "up"
  },
  {
    "interface_name": "wlan0",
    "ip": "192.168.1.22",
    "kind": "wifi",
    "enrolled": true,
    "proxy_id": "123e4567-e89b-12d3-a456-426614174000",
    "gateway": null,
    "link_state": "down"
  }
]
```

- `kind` is one of `ethernet`, `wifi`, `other`.
- `enrolled` is `true` when a persisted proxy already exists for the interface;
  `proxy_id` then holds its id.
- `gateway` is the interface's default IPv4 gateway (`null` when none).
- `link_state` is one of `up`, `down`, `unknown` (sysfs `operstate`, falling
  back to `carrier`; `unknown` when neither is readable).
- WWAN (dongle/modem) interfaces are excluded, as are interfaces currently used
  by an enrolled dongle.

#### Get Local Connection Detail

**GET** `/api/v1/local-connections/{interface_name}`

Returns the full detail of a single host interface: link state, gateway,
effective DNS servers, the enrolled proxy (if any) and its health.

**Response**
```json
{
  "interface_name": "eth0",
  "ip": "192.168.1.10",
  "kind": "ethernet",
  "gateway": "192.168.1.1",
  "link_state": "up",
  "dns": ["192.168.1.1", "8.8.8.8"],
  "enrolled": true,
  "proxy_id": "123e4567-e89b-12d3-a456-426614174000",
  "proxy": {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "source_type": "local",
    "source_ref": "eth0",
    "interface_name": "eth0",
    "container_name": "broxy4g-proxy-...",
    "listen_port": 8080,
    "state": "CONFIGURED",
    "enabled": true
  },
  "proxy_health": "healthy",
  "proxy_health_reason": null
}
```

- `dns` is the per-interface DNS from `resolvectl`, falling back to the
  `nameserver` entries of `/etc/resolv.conf`; `null` when unavailable.
- `proxy` is the full proxy detail (same shape as `GET /api/v1/proxies/{id}`),
  or `null` when the interface is not enrolled.
- `proxy_health` is one of `healthy`, `unhealthy`, `unknown` — `null` when no
  proxy is enrolled. `proxy_health_reason` explains an `unhealthy`/`unknown`
  result.
- Returns `404 Not Found` with a clean business message
  (`interface inconnue: <name>`) when the interface does not exist on the host.

#### Enroll Local Connection

**POST** `/api/v1/local-connections`

Enrolls a host interface, creating (or reusing) its proxy and pinning it to the
interface.

**Request**
```json
{
  "interface_name": "eth0",
  "enabled": true
}
```

- `enabled` is optional and defaults to the proxy's existing/desired state.

**Response**
```json
{
  "proxy": {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "source": "LOCAL",
    "interface_name": "eth0",
    "port": 8080,
    "route_table_id": 10001,
    "fwmark": 1
  },
  "password": "one-time-proxy-password"
}
```

- Returns `201 Created` when a new proxy is provisioned, `200 OK` when the
  interface was already enrolled (idempotent).
- The operation is idempotent: re-enrolling the same interface reuses the
  existing proxy rather than creating a second one.
- Returns `422 Unprocessable Entity` with a clean business message when the
  interface is unknown or has no usable IPv4 gateway.

#### Unenroll Local Connection

**DELETE** `/api/v1/local-connections/{interface_name}`

Removes the interface's proxy, releases its routing pinning and frees the
routing slot. Idempotent.

**Response**

`204 No Content` (no body).

### Proxy Management

#### Create Proxy

**POST** `/api/v1/dongles/{dongle_id}/proxy`

Creates a new proxy for a dongle, or returns the existing one unchanged.

**Request** (optional body)
```json
{
  "enabled": true
}
```

- `enabled` is optional and defaults to `true`.

**Response**
```json
{
  "proxy": {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "dongle_id": "123e4567-e89b-12d3-a456-426614174001",
    "source_type": "dongle",
    "source_ref": "123456789012345",
    "interface_name": null,
    "container_name": "broxy4g-proxy-123e4567-e89b-12d3-a456-426614174000",
    "listen_port": 31201,
    "auth_user": "khmtpznwqrbv",
    "state": "RUNNING",
    "enabled": true,
    "network_ip": "172.28.0.101",
    "created_at": "2026-10-17T10:00:00Z",
    "updated_at": "2026-10-17T10:00:05Z"
  },
  "password": "one-time-proxy-password"
}
```

- Returns `201 Created` with a one-time `password` when a new proxy is
  provisioned. The password is returned only once.
- Returns `200 OK` with `password: null` when the dongle already has a proxy
  (idempotent — the existing credentials are left unchanged and the running
  container is not restarted).
- `state` is one of `STOPPED`, `STARTING`, `RUNNING`, `CRASHED`, `REMOVED`.
- Returns `404 Not Found` with a clean business message when the dongle is
  unknown.

#### Start Proxy

**POST** `/api/v1/proxies/{id}/start`

Starts a proxy.

**Response**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "state": "STARTING"
}
```

#### Stop Proxy

**POST** `/api/v1/proxies/{id}/stop`

Stops a proxy.

**Response**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "state": "STOPPING"
}
```

#### Remove Proxy

**POST** `/api/v1/proxies/{id}/remove`

Removes a proxy.

**Response**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "state": "REMOVING"
}
```

#### Rotate Proxy Credentials

**POST** `/api/v1/proxies/{proxy_id}/rotate`

Rotates the proxy credentials. The proxy password is stored only as a hash
(`password_hash`), so it can never be retrieved — this endpoint generates a
new one instead.

**Response**
```json
{
  "proxy": {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "auth_user": "khmtpznwqrbv",
    "state": "RUNNING"
  },
  "password": "one-time-proxy-password"
}
```

- Issues a new `auth_user` together with a new password.
- The password is encrypted at rest (Fernet) so it can be retrieved again —
  see *Retrieve Proxy Credentials* below. Rotation still generates a fresh
  pair and invalidates the previous one.
- Existing clients are disconnected and must be updated with the new
  credentials.

#### Retrieve Proxy Credentials

**GET** `/api/v1/proxies/{proxy_id}/credentials`

Returns the proxy's current `auth_user` and password, decrypted at rest.
Protected by the same Basic Auth as every other endpoint, and audited
(server-side) as an admin action.

**Response**
```json
{
  "proxy": {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "auth_user": "khmtpznwqrbv",
    "state": "RUNNING"
  },
  "password": "stored-proxy-password"
}
```

- Response headers are `Cache-Control: no-store` and `Pragma: no-cache`: the
  body must never be cached.
- Returns `404 Not Found` when the proxy is unknown.
- Returns `409 Conflict` when the proxy exists but its password cannot be
  retrieved — i.e. it predates encrypted storage (or the value is unreadable,
  e.g. a rotated/deleted key). The `detail` carries a clean business message
  such as `identifiants non récupérables : proxy antérieur au stockage
  chiffré, régénérer les identifiants`; call *Rotate Proxy Credentials* to
  obtain new, retrievable credentials.
- The plaintext password is never written to any log.

### Settings

Global settings, all under `/api/v1/settings`, protected by the same Basic Auth.

#### Get Proxy Auto-Create

**GET** `/api/v1/settings/proxy-auto-create`

Returns whether a proxy is automatically provisioned for a newly enrolled dongle.

**Response**
```json
{
  "enabled": true
}
```

#### Set Proxy Auto-Create

**PUT** `/api/v1/settings/proxy-auto-create`

Enables or disables automatic proxy creation on dongle enrollment.

**Request**
```json
{
  "enabled": true
}
```

**Response**: the resulting setting (same shape as `GET`).

#### Get Proxy Listen Interface

**GET** `/api/v1/settings/proxy-listen-interface`

Returns the host interface on which proxy ports are published. `null` means the
default `0.0.0.0` (all interfaces).

**Response**
```json
{
  "interface_name": "eth0"
}
```

#### Set Proxy Listen Interface

**PUT** `/api/v1/settings/proxy-listen-interface`

Sets the host interface on which the proxy ports are published. Only `ethernet`
and `wifi` interfaces are accepted (never a dongle/WWAN, nor `lo`/`docker`/`veth`).
Sending `null` (or an empty value) resets the setting to the default `0.0.0.0`.

**Request**
```json
{
  "interface_name": "eth0"
}
```

**Response**: the resulting setting (same shape as `GET`).

- Returns `422 Unprocessable Entity` with a clean business message when the
  interface is unknown, is not of type `ethernet`/`wifi`, or has no IPv4 address.
- On success the change is applied to all currently active proxies (a brief
  restart), re-binding their published ports to the chosen interface's IPv4.
- If the chosen interface later disappears or loses its IPv4, the proxy port is
  simply **not published** (the container stays active) and the publication is
  re-applied automatically by the reconciliation when the interface returns.
  See `docs/networking.md` → *Listen Interface (ingress)*.

## Error Codes

| Code | Description |
|--------|-------------|
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 409 | Conflict (e.g., duplicate enrollment) |
| 422 | Unprocessable Entity (e.g., incomplete identity, unknown `transport`) |
| 500 | Internal Server Error |