from dataclasses import dataclass
from uuid import UUID


@dataclass
class SummaryMetrics:
    public_ip: str | None = None
    signal_quality: int | None = None


class MetricsSnapshot:
    """In-memory per-dongle summary metrics cache.

    Written by the reconciliation worker, read by the API list endpoint so
    listing dongles never triggers expensive ping / public-IP lookups.
    """

    def __init__(self) -> None:
        self._metrics: dict[UUID, SummaryMetrics] = {}

    def update(self, dongle_id: UUID, public_ip: str | None, signal_quality: int | None) -> None:
        self._metrics[dongle_id] = SummaryMetrics(public_ip=public_ip, signal_quality=signal_quality)

    def get(self, dongle_id: UUID) -> SummaryMetrics | None:
        return self._metrics.get(dongle_id)

    def remove(self, dongle_id: UUID) -> None:
        self._metrics.pop(dongle_id, None)
