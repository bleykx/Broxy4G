from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ModemManagerAdapter(Protocol):
    async def list_modems(self) -> list["ModemInfo"]:
        ...

    async def get_modem(self, modem_path: str) -> Optional["ModemInfo"]:
        ...

    async def connect(self, modem_path: str, apn: str, auth: str | None) -> None:
        ...

    async def disconnect(self, modem_path: str) -> None:
        ...

    async def reset_modem(self, modem_path: str) -> None:
        ...

    async def get_signal(self, modem_path: str) -> "SignalQuality":
        ...

    async def get_imei(self, modem_path: str) -> str | None:
        ...

    async def get_iccid(self, modem_path: str) -> str | None:
        ...

    async def get_bearer_ip(self, modem_path: str) -> Optional["BearerIpConfig"]:
        ...

    async def get_session_id(self, modem_path: str) -> str | None:
        ...


@dataclass
class BearerIpConfig:
    """Configuration IPv4 d'un bearer ModemManager.

    ``method`` transporte la méthode de configuration IP
    (``bearer.ipv4-config.method``) : ``"ppp"`` signale une session PPP sans
    interface réseau (port net absent chez ModemManager), sans IPv4 utilisable.
    """

    address: str
    prefix: int
    gateway: str | None = None
    dns: list[str] = field(default_factory=list)
    interface: str | None = None
    mtu: int | None = None
    path: str | None = None
    method: str | None = None


@dataclass
class ModemInfo:
    path: str
    imei: str | None
    iccid: str | None
    usb_serial: str | None
    state: str
    interface_name: str | None
    imsi: str | None = None
    operator_id: str | None = None
    operator_name: str | None = None
    # Chemin sysfs du périphérique USB (champ ``device`` de ``mmcli -m``).
    # Existe même sans port net (bearer PPP) : c'est la clé de la
    # ré-énumération USB douce. Jamais de clé d'identité — confort/ressource.
    device_path: str | None = None


@dataclass
class SignalQuality:
    percent: int | None = None
    rsrp: int | None = None
    rsrq: int | None = None
    sinr: int | None = None
    rssi: int | None = None


class FakeModemManager:
    def __init__(self):
        self._modems: dict[str, ModemInfo] = {}
        self._signal: dict[str, SignalQuality] = {}
        self._state: dict[str, str] = {}
        self._bearer_ips: dict[str, BearerIpConfig] = {}
        self._session_seq: dict[str, int] = {}
        self.reset_calls: list[str] = []
        self._vanish_remaining: dict[str, int] = {}
        self._hide_on_reset: dict[str, bool] = {}

    def set_bearer_ip(self, path: str, config: BearerIpConfig):
        self._bearer_ips[path] = config

    def add_modem(self, info: ModemInfo):
        self._modems[info.path] = info
        self._signal[info.path] = SignalQuality()
        self._state[info.path] = info.state
        self._session_seq[info.path] = 0

    def hide_on_reset(self, path: str) -> None:
        """Test helper : le modem ne réapparaît plus après un reset."""
        self._hide_on_reset[path] = True

    def simulate_usb_reevaluation(self, path: str) -> None:
        """Test helper : ré-énumération USB (disparaît 1 poll, puis réapparaît)."""
        self._vanish_remaining[path] = 1

    def forget(self, path: str) -> None:
        """Test helper : le modem disparaît définitivement de ModemManager."""
        self._modems.pop(path, None)
        self._vanish_remaining.pop(path, None)

    def set_signal(self, path: str, quality: SignalQuality):
        self._signal[path] = quality

    def set_state(self, path: str, state: str):
        self._state[path] = state

    async def list_modems(self) -> list[ModemInfo]:
        # Un modem en cours de disparition (ré-énumération USB post-reset) est
        # sauté, puis réapparaît au prochain appel.
        modems: list[ModemInfo] = []
        for path, modem in self._modems.items():
            remaining = self._vanish_remaining.get(path, 0)
            if remaining > 0:
                self._vanish_remaining[path] = remaining - 1
                continue
            modems.append(modem)
        return modems

    async def get_modem(self, modem_path: str) -> ModemInfo | None:
        modem = self._modems.get(modem_path)
        if modem is None:
            return None
        state = self._state.get(modem_path, modem.state)
        if state != modem.state:
            modem = ModemInfo(
                path=modem.path,
                imei=modem.imei,
                iccid=modem.iccid,
                usb_serial=modem.usb_serial,
                state=state,
                interface_name=modem.interface_name,
                imsi=modem.imsi,
                operator_id=modem.operator_id,
                operator_name=modem.operator_name,
                device_path=modem.device_path,
            )
            self._modems[modem_path] = modem
        return modem

    async def connect(self, modem_path: str, apn: str, auth: str | None) -> None:
        self._state[modem_path] = "connected"
        self._session_seq[modem_path] = self._session_seq.get(modem_path, 0) + 1

    async def disconnect(self, modem_path: str) -> None:
        self._state[modem_path] = "disconnected"

    async def reset_modem(self, modem_path: str) -> None:
        """Simule le reset modem USB : le modem disparaît (sauf ``hide_on_reset``)."""
        self.reset_calls.append(modem_path)
        self._state[modem_path] = "disconnected"
        if self._hide_on_reset.get(modem_path, False):
            self._modems.pop(modem_path, None)
            self._vanish_remaining.pop(modem_path, None)
        else:
            self._vanish_remaining[modem_path] = 1

    async def get_signal(self, modem_path: str) -> SignalQuality:
        return self._signal[modem_path]

    async def get_imei(self, modem_path: str) -> str | None:
        modem = self._modems.get(modem_path)
        return modem.imei if modem else None

    async def get_iccid(self, modem_path: str) -> str | None:
        modem = self._modems.get(modem_path)
        return modem.iccid if modem else None

    async def get_bearer_ip(self, modem_path: str) -> BearerIpConfig | None:
        return self._bearer_ips.get(modem_path)

    async def get_session_id(self, modem_path: str) -> str | None:
        if self._state.get(modem_path) != "connected":
            return None
        return f"{modem_path}:{self._session_seq.get(modem_path, 0)}"


# Real implementation
