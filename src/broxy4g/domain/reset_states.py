from enum import Enum
from uuid import uuid4


class ResetState(Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    DONE = "DONE"
    ERROR = "ERROR"

# PENDING n'est PAS un état stocké : c'est la réponse à une demande concurrente
# tant que le verrou est tenu (state == RUNNING)

ALLOWED_RESET_TRANSITIONS: dict[ResetState, set[ResetState]] = {
    ResetState.IDLE: {ResetState.RUNNING},
    ResetState.RUNNING: {ResetState.DONE, ResetState.ERROR},
    ResetState.DONE: set(),  # terminal
    ResetState.ERROR: set(),  # terminal
}

class InvalidResetTransition(Exception):
    def __init__(self, from_state: ResetState, to_state: ResetState):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Transition reset invalide : {from_state.value} → {to_state.value}")

def can_reset_transition(from_state: ResetState, to_state: ResetState) -> bool:
    return to_state in ALLOWED_RESET_TRANSITIONS.get(from_state, set())

def reset_transition(from_state: ResetState, to_state: ResetState) -> ResetState:
    """Effectue la transition reset, lève InvalidResetTransition si non autorisée."""
    if not can_reset_transition(from_state, to_state):
        raise InvalidResetTransition(from_state, to_state)
    return to_state

class ResetInProgressError(Exception):
    """Lève quand un reset tourne déjà pour ce dongle (verrou déjà tenu)."""
    def __init__(self, dongle_id: str):
        self.dongle_id = dongle_id
        super().__init__(f"Reset déjà en cours pour dongle {dongle_id}")


class ResetLock:
    """Verrou exclusif de reset par dongle.

    - acquire(dongle_id) : lève ResetInProgressError si déjà verrouillé
    - release(dongle_id, token) : libère le verrou si le token correspond
    - is_locked(dongle_id) : True si un reset est en cours
    """

    def __init__(self) -> None:
        self._locks: dict[str, str] = {}

    def acquire(self, dongle_id: str) -> str:
        if dongle_id in self._locks:
            raise ResetInProgressError(dongle_id)
        token = uuid4().hex
        self._locks[dongle_id] = token
        return token

    def release(self, dongle_id: str, token: str) -> None:
        current = self._locks.get(dongle_id)
        if current is None or current != token:
            raise ValueError(f"Token invalide pour dongle {dongle_id}")
        del self._locks[dongle_id]

    def is_locked(self, dongle_id: str) -> bool:
        return dongle_id in self._locks