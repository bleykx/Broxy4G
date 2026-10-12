"""Pure parsing functions for mmcli output.

No subprocess calls, no filesystem access — pure text → dataclasses.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from broxy4g.adapters.modem_manager import ModemInfo, SignalQuality

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# USB device path fragment: usb1/1-7 → group(1) = "1-7"
_USB_DEV_RE = re.compile(r"usb\d+/([\d-]+)")

# Key-value after pipe: everything before the first ': ' is the key
# Matches: "state: registered" and "primary sim path: /org/..."
_KEY_LINE_RE = re.compile(r"^([^:]+?):\s*(.*)$")

# Known mmcli section names — used to distinguish section headers from key-value lines
_KNOWN_SECTIONS = frozenset({
    "General", "Hardware", "System", "Numbers", "Status",
    "Modes", "Bands", "IP", "3GPP", "3GPP EPS", "SIM",
})


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

def parse_mmcli_modem(
    text: str,
    read_usb_serial: Callable[[str | None], str | None] | None = None,
    resolve_interface: Callable[[str | None], str | None] | None = None,
    iccid: str | None = None,
) -> ModemInfo:
    """Parse the output of ``mmcli -m <path>`` into a ModemInfo."""

    fields: dict[str, str] = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        # Split on first pipe
        after_pipe = line.split("|", 1)[1]
        stripped = after_pipe.strip()
        # Check if the first word after | is a known section name
        first_word = stripped.split()[0] if stripped.split() else ""
        if first_word in _KNOWN_SECTIONS:
            continue
        m = _KEY_LINE_RE.match(stripped)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip()
            fields[key] = value

    # IMEI: prefer 3GPP `imei`, fallback `equipment id`
    imei = fields.get("imei") or fields.get("equipment id")

    # State from Status section
    state = fields.get("state", "unknown")

    # Device sysfs path (System section)
    device_path = fields.get("device")

    # USB serial (external reader)
    usb_serial = _extract_usb_serial(device_path, read_usb_serial)

    # Interface name (external resolver)
    interface_name = _resolve_interface_name(device_path, resolve_interface)

    return ModemInfo(
        path="",  # filled by caller
        imei=imei,
        iccid=iccid,
        usb_serial=usb_serial,
        state=state,
        interface_name=interface_name,
        operator_id=fields.get("operator id"),
        operator_name=fields.get("operator name"),
        device_path=device_path,
    )


def parse_mmcli_signal(text: str) -> SignalQuality:
    """Parse signal quality from ``mmcli -m <path>`` output.

    Returns SignalQuality with percent set; the other fields stay None
    because this mmcli version does not expose RSRP/RSRQ/SINR/RSSI in
    the default output.
    """
    m = re.search(r"signal quality:\s*(\d+)%", text)
    if m:
        return SignalQuality(percent=int(m.group(1)), rsrp=None, rsrq=None, sinr=None, rssi=None)
    return SignalQuality(percent=None, rsrp=None, rsrq=None, sinr=None, rssi=None)


def apply_mmcli_signal_detail(quality: SignalQuality, fields: dict[str, str]) -> SignalQuality:
    """Enrich *quality* with RSRP/RSRQ/SINR/RSSI from ``--signal-get -K`` fields.

    Falls back LTE → 5G (→ UMTS/GSM for RSSI); ``None``/``""``/``"--"``/unparsable
    values are treated as absent and skipped.
    """

    def _first_int(*keys: str) -> int | None:
        for key in keys:
            raw = fields.get(key)
            if raw in (None, "", "--"):
                continue
            try:
                return int(raw)
            except ValueError:
                continue
        return None

    return SignalQuality(
        percent=quality.percent,
        rsrp=_first_int("modem.signal.lte.rsrp", "modem.signal.5g.rsrp"),
        rsrq=_first_int("modem.signal.lte.rsrq", "modem.signal.5g.rsrq"),
        sinr=_first_int("modem.signal.lte.snr", "modem.signal.5g.snr"),
        rssi=_first_int(
            "modem.signal.lte.rssi", "modem.signal.5g.rssi",
            "modem.signal.umts.rssi", "modem.signal.gsm.rssi",
        ),
    )


def parse_mmcli_sim(text: str) -> dict[str, str]:
    """Parse ``mmcli -i <sim-path>`` output into key-value dict."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        after_pipe = line.split("|", 1)[1]
        stripped = after_pipe.strip()
        if not stripped:
            continue
        m = _KEY_LINE_RE.match(stripped)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip()
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Sub- parsers (monkeypatchable stubs)
# ---------------------------------------------------------------------------

def _extract_usb_serial(
    device_path: str | None,
    read_fn: Callable[[str | None], str | None] | None = None,
) -> str | None:
    """Extract USB serial from sysfs device path.

    If *device_path* is None or doesn't match ``usbN/X-Y``, return None.
    If *read_fn* is provided (not None), call it with device_path and return
    its result (for testing).
    """
    if not device_path:
        return None
    m = _USB_DEV_RE.search(device_path)
    if not m:
        return None
    if read_fn is not None:
        return read_fn(device_path)
    return None


def _resolve_interface_name(
    device_path: str | None,
    resolve_fn: Callable[[str | None], str | None] | None = None,
) -> str | None:
    """Resolve interface name from sysfs device path.

    If *device_path* is None return None.
    If *resolve_fn* is provided (not None), call it with device_path and
    return its result (for testing).
    """
    if not device_path:
        return None
    if resolve_fn is not None:
        return resolve_fn(device_path)
    return None
