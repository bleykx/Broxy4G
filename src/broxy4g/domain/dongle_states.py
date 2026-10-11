from enum import Enum


class DongleState(Enum):
    DISCOVERED = "DISCOVERED"
    UNENROLLED = "UNENROLLED"
    CONFIGURING = "CONFIGURING"
    CONNECTING = "CONNECTING"
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    RESETTING = "RESETTING"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"

# Transitions autorisées : dict[DongleState, set[DongleState]]
ALLOWED_TRANSITIONS: dict[DongleState, set[DongleState]] = {
    DongleState.DISCOVERED: {DongleState.CONFIGURING, DongleState.UNENROLLED, DongleState.OFFLINE},
    DongleState.UNENROLLED: {DongleState.CONFIGURING, DongleState.OFFLINE},
    DongleState.CONFIGURING: {DongleState.CONNECTING, DongleState.ERROR, DongleState.OFFLINE},
    DongleState.CONNECTING: {DongleState.ONLINE, DongleState.ERROR, DongleState.OFFLINE},
    DongleState.ONLINE: {DongleState.DEGRADED, DongleState.RESETTING, DongleState.OFFLINE, DongleState.ERROR},
    DongleState.DEGRADED: {
        DongleState.ONLINE,
        DongleState.CONNECTING,
        DongleState.RESETTING,
        DongleState.OFFLINE,
        DongleState.ERROR,
    },
    DongleState.RESETTING: {DongleState.ONLINE, DongleState.ERROR, DongleState.OFFLINE, DongleState.CONNECTING},
    DongleState.OFFLINE: {DongleState.CONNECTING, DongleState.OFFLINE, DongleState.RESETTING},
    DongleState.ERROR: {DongleState.CONNECTING, DongleState.OFFLINE, DongleState.RESETTING},
}

class InvalidStateTransition(Exception):
    def __init__(self, from_state: DongleState, to_state: DongleState):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Transition invalide : {from_state.value} → {to_state.value}")

def can_transition(from_state: DongleState, to_state: DongleState) -> bool:
    """Vérifie si la transition est autorisée."""
    return to_state in ALLOWED_TRANSITIONS.get(from_state, set())

def transition(from_state: DongleState, to_state: DongleState) -> DongleState:
    """Effectue la transition, lève InvalidStateTransition si non autorisée.
    Retourne le nouvel état."""
    if not can_transition(from_state, to_state):
        raise InvalidStateTransition(from_state, to_state)
    return to_state