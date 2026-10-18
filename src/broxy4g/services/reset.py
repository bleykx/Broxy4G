import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from broxy4g.domain.degraded_arbiter import ProbeVerdict
from broxy4g.domain.dongle_states import DongleState, InvalidStateTransition, can_transition
from broxy4g.domain.post_done_window import ResetPostDoneWindow
from broxy4g.domain.reset_states import ResetInProgressError, ResetLock
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.connection import ConnectionService
from broxy4g.services.metrics_snapshot import MetricsSnapshot
from broxy4g.services.proxy_lifecycle import ProxyLifecycleService
from broxy4g.services.reset_throttle import CAP, ResetThrottle
from broxy4g.services.uplink_probe import PublicIpVerdict

logger = logging.getLogger(__name__)


@dataclass
class ResetOutcome:
    status: str
    message: str | None = None


def _throttled(reason: str, throttle: ResetThrottle) -> ResetOutcome:
    """Statut métier d'un reset refusé par la garde anti-tempête (pas de matériel touché)."""
    if reason == CAP:
        return ResetOutcome(
            "ERROR",
            f"reset refusé : trop de resets rapprochés (max {throttle.max_per_window} par {throttle.window_seconds:g}s)",
        )
    return ResetOutcome(
        "PENDING",
        f"reset en cooldown : un reset récent (< {throttle.cooldown_seconds:g}s) est déjà appliqué",
    )


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
        throttle: ResetThrottle | None = None,
        done_window: ResetPostDoneWindow | None = None,
        ip_wait_attempts: int = 15,
        ip_wait_delay: float = 2.0,
    ):
        self.dongle_repo = dongle_repo
        self.proxy_repo = proxy_repo
        self.connection = connection
        self.proxy = proxy
        self.lock = lock
        self.metrics_snapshot = metrics_snapshot
        self.reset_timeout = reset_timeout
        self.throttle = throttle
        # Fenêtre post-DONE TOUJORS active : 15 s de répit après un reset réussi.
        self.done_window = done_window or ResetPostDoneWindow()
        self.ip_wait_attempts = ip_wait_attempts
        self.ip_wait_delay = ip_wait_delay

    async def reset(self, dongle_id: UUID) -> ResetOutcome:
        # Garde anti-tempête : le refus précède le verrou et ne touche pas le matériel.
        key = str(dongle_id)
        if self.throttle is not None:
            reason = self.throttle.reject(key)
            if reason is not None:
                logger.info(
                    "reset dongle %s refusé par la garde anti-tempête (%s)", dongle_id, reason
                )
                return _throttled(reason, self.throttle)
        if self.done_window.in_window(key):
            logger.info("reset dongle %s refusé : fenêtre de répit post-reset en cours", dongle_id)
            return ResetOutcome("PENDING", "fenêtre de répit post-reset : un reset vient d'aboutir")

        try:
            token = self.lock.acquire(key)
        except ResetInProgressError:
            return ResetOutcome("PENDING", "reset déjà en cours")

        if self.throttle is not None:
            # Le verrou est tenu : ce reset va réellement s'exécuter.
            self.throttle.record(key)

        try:
            outcome = await asyncio.wait_for(self._run_reset(dongle_id), timeout=self.reset_timeout)
        except TimeoutError:
            # Timeout : le conteneur proxy reste arrêté mais ``enabled`` est
            # conservé — la réconciliation le restaurera (pas de proxy sans
            # uplink fiable).
            outcome = ResetOutcome("ERROR", f"reset expiré après {self.reset_timeout:g}s")
        finally:
            self.lock.release(key, token)
        if outcome.status == "DONE":
            self.done_window.mark_done(key)
        return outcome

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
            # Capture de l'état désiré AVANT la pause : un proxy désactivé par
            # l'opérateur doit rester désactivé après le reset.
            was_enabled = bool(existing_proxy.enabled) if existing_proxy else False
            if existing_proxy:
                await self.proxy.pause(existing_proxy.id)

            connected = await self.connection.reset_modem(dongle_id)
            if connected.state == DongleState.ERROR:
                # Échec : le conteneur reste arrêté mais ``enabled`` est
                # conservé — la réconciliation le restaurera dès que le dongle
                # aura de nouveau un uplink utilisable.
                return ResetOutcome("ERROR", connected.last_error or "échec reconnexion après reset")

            if was_enabled:
                await self.proxy.resume(existing_proxy.id)

            metrics = await self.connection.get_metrics(dongle_id)
            probe = await self._await_public_ip(
                dongle_id, PublicIpVerdict(metrics.public_ip_verdict, metrics.public_ip)
            )
            if self.metrics_snapshot is not None:
                signal = metrics.signal_quality.percent if metrics.signal_quality else None
                self.metrics_snapshot.update(dongle_id, probe.ip, signal)
            try:
                new_session = await self.connection.get_session_id(dongle_id)
            except Exception:
                new_session = None
            logger.info(
                "reset dongle %s: bearer %s -> %s, ip %s -> %s",
                dongle_id, baseline_session, new_session, baseline_ip, probe.ip,
            )
            if probe.verdict is not ProbeVerdict.OK:
                logger.info(
                    "reset dongle %s: IP publique non vérifiée après reset (%s)",
                    dongle_id, getattr(probe.verdict, "value", probe.verdict),
                )
                # L'IP publique n'est pas vérifiée : on ne marque PAS le dongle
                # ERROR (il reste ONLINE) — le marquer déclencherait une
                # ré-énumération USB destructive (usb_recovery) sur un lien
                # qui fonctionne.
                return ResetOutcome("ERROR", "IP publique non vérifiée après reset (sonde échouée)")
            return ResetOutcome("DONE")
        except Exception as exc:
            # Échec : pas de reprise du conteneur (uplink non fiable) ; il
            # reste arrêté avec ``enabled`` conservé, la réconciliation le
            # restaurera plus tard.
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

    async def _await_public_ip(self, dongle_id: UUID, initial: PublicIpVerdict) -> PublicIpVerdict:
        """Attend la propagation de l'IP publique (verdict de sonde ``OK``)."""
        probe = initial
        for _ in range(self.ip_wait_attempts):
            if probe.verdict is ProbeVerdict.OK:
                return probe
            await asyncio.sleep(self.ip_wait_delay)
            try:
                probe = await self.connection.get_public_ip_verdict(dongle_id)
            except Exception:
                logger.warning("Sonde IP publique indécidable après reset dongle %s", dongle_id)
                probe = PublicIpVerdict(ProbeVerdict.FAILED, None)
        return probe

    async def reset_many(self, dongle_ids: list[UUID]) -> list[tuple[UUID, ResetOutcome]]:
        """Reset plusieurs dongles en parallèle. Un reset par dongle, indépendant.

        Déduplique en conservant l'ordre de première apparition.
        Une erreur d'un dongle n'affecte pas les autres."""
        unique_ids = list(dict.fromkeys(dongle_ids))
        outcomes = await asyncio.gather(*(self.reset(did) for did in unique_ids))
        return list(zip(unique_ids, outcomes, strict=True))
