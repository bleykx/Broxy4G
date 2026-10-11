from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .dongle_states import DongleState, transition
from .identity import IdentityMaterial


@dataclass
class Dongle:
    identity: IdentityMaterial
    label: str
    state: DongleState = DongleState.DISCOVERED
    id: UUID = field(default_factory=uuid4)
    apn: str | None = None
    apn_auth: str | None = None  # "none", "pap", "chap"
    route_table_id: int | None = None
    fwmark: int | None = None
    interface_name: str | None = None  # instable, jamais clé
    proxy_id: UUID | None = None
    last_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def change_state(self, new_state: DongleState) -> None:
        """Effectue la transition d'état, lève InvalidStateTransition si non autorisée."""
        self.state = transition(self.state, new_state)
        self.touch()

    def fail(self, message: str) -> None:
        """Enregistre le détail de la dernière erreur et passe l'état en ERROR."""
        self.last_error = message
        self.change_state(DongleState.ERROR)

    def clear_error(self) -> None:
        """Efface le détail de la dernière erreur (succès)."""
        self.last_error = None

    @property
    def identity_key(self) -> str:
        """Clé d'identité matérielle persistante."""
        return self.identity.primary_key

    def touch(self) -> None:
        """Met à jour updated_at."""
        self.updated_at = datetime.now(UTC)