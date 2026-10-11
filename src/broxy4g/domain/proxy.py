from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from uuid import UUID, uuid4


class ProxyState(Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    CRASHED = "CRASHED"
    REMOVED = "REMOVED"

ALLOWED_PROXY_TRANSITIONS: dict[ProxyState, set[ProxyState]] = {
    ProxyState.STOPPED: {ProxyState.STARTING, ProxyState.REMOVED},
    ProxyState.STARTING: {ProxyState.RUNNING, ProxyState.CRASHED, ProxyState.STOPPED},
    ProxyState.RUNNING: {ProxyState.STOPPED, ProxyState.CRASHED, ProxyState.REMOVED},
    ProxyState.CRASHED: {ProxyState.STARTING, ProxyState.STOPPED, ProxyState.REMOVED},
    ProxyState.REMOVED: set(),
}

class InvalidProxyTransition(Exception):
    def __init__(self, from_state: ProxyState, to_state: ProxyState):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Transition proxy invalide : {from_state.value} → {to_state.value}")

class ProxySource(StrEnum):
    DONGLE = "dongle"
    LOCAL = "local"

@dataclass
class Proxy:
    dongle_id: UUID | None
    container_name: str
    listen_port: int
    state: ProxyState = ProxyState.STOPPED
    id: UUID = field(default_factory=uuid4)
    auth_user: str | None = None
    password_hash: str | None = None  # format 3proxy CR ; jamais le mot de passe clair
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source_type: ProxySource = ProxySource.DONGLE
    source_ref: str | None = None
    interface_name: str | None = None
    enabled: bool = True
    config_path: str | None = None
    compose_path: str | None = None
    network_ip: str | None = None
    # Uplink local (interface hôte) : table de routage dédiée + marque firewall
    # épinglant le trafic du proxy à cette interface.
    route_table_id: int | None = None
    fwmark: int | None = None

    def change_state(self, new_state: ProxyState) -> None:
        if new_state not in ALLOWED_PROXY_TRANSITIONS.get(self.state, set()):
            raise InvalidProxyTransition(self.state, new_state)
        self.state = new_state
        self.touch()

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)