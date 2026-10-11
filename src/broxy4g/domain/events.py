from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4


class EventType(Enum):
    DONGLE_ENROLLED = "DONGLE_ENROLLED"
    DONGLE_DEACTIVATED = "DONGLE_DEACTIVATED"
    DONGLE_STATE_CHANGED = "DONGLE_STATE_CHANGED"
    RESET_STARTED = "RESET_STARTED"
    RESET_DONE = "RESET_DONE"
    RESET_ERROR = "RESET_ERROR"
    PROXY_CREATED = "PROXY_CREATED"
    PROXY_REMOVED = "PROXY_REMOVED"
    PROXY_STATE_CHANGED = "PROXY_STATE_CHANGED"
    CONNECTION_STARTED = "CONNECTION_STARTED"
    CONNECTION_STOPPED = "CONNECTION_STOPPED"
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
    RECONCILIATION_COMPLETED = "RECONCILIATION_COMPLETED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"

@dataclass(frozen=True)
class DomainEvent:
    event_type: EventType
    dongle_id: UUID
    id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    detail: str | None = None