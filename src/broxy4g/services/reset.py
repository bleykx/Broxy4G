import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from broxy4g.domain.dongle_states import DongleState, InvalidStateTransition, can_transition
from broxy4g.domain.reset_states import ResetInProgressError, ResetLock
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.connection import ConnectionService
from broxy4g.services.metrics_snapshot import MetricsSnapshot
from broxy4g.services.proxy_lifecycle import ProxyLifecycleService

logger = logging.getLogger(__name__)


@dataclass
class ResetOutcome:
    status: str
    message: str | None = None


class ResetService:
    def __init__(
        self,
        dongle_repo: DongleRepository,
        proxy_repo: ProxyRepository,
        connection: ConnectionService,
        proxy: ProxyLifecycleService,
        lock: ResetLock,
        metrics_snapshot: MetricsSnapshot | None = None,
        reset_timeout: float = 300.0,
    ):
        self.dongle_repo = dongle_repo
        self.proxy_repo = proxy_repo
        self.connection = connection
        self.proxy = proxy
        self.lock = lock
        self.metrics_snapshot = metrics_snapshot
        self.reset_timeout = reset_timeout

    async def reset(self, dongle_id: UUID) -> ResetOutcome:
        try:
            token = self.lock.acquire(str(dongle_id))
        except ResetInProgressError:
            return ResetOutcome("PENDING", "reset déjà en cours")

        try:
            return await asyncio.wait_for(self._run_reset(dongle_id), timeout=self.reset_timeout)
        except TimeoutError:
            return ResetOutcome("ERROR", f"reset expiré après {self.reset_timeout:g}s")
        finally:
            self.lock.release(str(dongle_id), token)

    async def _run_reset(self, dongle_id: UUID) -> ResetOutcome:
        dongle = await self.dongle_repo.get_by_id(dongle_id)
        if not dongle:
            return ResetOutcome("ERROR", "dongle introuvable")

        try:
            dongle.change_state(DongleState.RESETTING)
        except InvalidStateTransition:
            return ResetOutcome("ERROR", f"reset impossible dans l'état {dongle.state.value}")
        await self.dongle_repo.save(dongle)

        # Métriques pré-reset (best-effort) : pour le log de la transition
        # bearer / IP publique. Le reset modem USB garantit une nouvelle session.
        try:
            baseline_session = await self.connection.get_session_id(dongle_id)
        except Exception:
            logger.warning("Échec lecture session data pré-reset dongle %s", dongle_id)
            baseline_session = None
        try:
            baseline_ip = await self.connection.get_public_ip(dongle_id)
        except Exception:
            logger.warning("Échec lecture IP publique pré-reset dongle %s", dongle_id)
            baseline_ip = None

        try:
            existing_proxy = await self.proxy_repo.get_by_dongle_id(dongle_id)
            if existing_proxy:
                await self.proxy.stop(existing_proxy.id)

            connected = await self.connection.reset_modem(dongle_id)
            if connected.state == DongleState.ERROR:
                return ResetOutcome("ERROR", connected.last_error or "échec reconnexion après reset")

            if existing_proxy and existing_proxy.enabled:
                await self.proxy.start(existing_proxy.id)

            metrics = await self.connection.get_metrics(dongle_id)
            if self.metrics_snapshot is not None:
                signal = metrics.signal_quality.percent if metrics.signal_quality else None
                self.metrics_snapshot.update(dongle_id, metrics.public_ip, signal)
            try:
                new_session = await self.connection.get_session_id(dongle_id)
            except Exception:
                new_session = None
            logger.info(
                "reset dongle %s: bearer %s -> %s, ip %s -> %s",
                dongle_id, baseline_session, new_session, baseline_ip, metrics.public_ip,
            )
            return ResetOutcome("DONE")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            try:
                d = await self.dongle_repo.get_by_id(dongle_id)
                if d:
                    d.last_error = message
                    if can_transition(d.state, DongleState.ERROR):
                        d.change_state(DongleState.ERROR)
                    await self.dongle_repo.save(d)
            except Exception:
                logger.exception("Échec de la récupération d'état après reset dongle %s", dongle_id)
            return ResetOutcome("ERROR", message)

    async def reset_many(self, dongle_ids: list[UUID]) -> list[tuple[UUID, ResetOutcome]]:
        """Reset plusieurs dongles en parallèle. Un reset par dongle, indépendant.

        Déduplique en conservant l'ordre de première apparition.
        Une erreur d'un dongle n'affecte pas les autres."""
        unique_ids = list(dict.fromkeys(dongle_ids))
        outcomes = await asyncio.gather(*(self.reset(did) for did in unique_ids))
        return list(zip(unique_ids, outcomes, strict=True))
