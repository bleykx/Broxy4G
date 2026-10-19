import asyncio
import logging
import math
from uuid import UUID

from broxy4g.domain.degraded_arbiter import ProbeVerdict
from broxy4g.domain.dongle_states import DongleState, InvalidStateTransition
from broxy4g.domain.post_done_window import ResetPostDoneWindow
from broxy4g.domain.reset_lockout import ResetLockout
from broxy4g.domain.reset_states import ResetInProgressError, ResetLock
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.connection import ConnectionService
from broxy4g.services.metrics_snapshot import MetricsSnapshot
from broxy4g.services.proxy_lifecycle import ProxyLifecycleService
from broxy4g.services.public_ip_gate import await_public_ip
from broxy4g.services.reset_failure_policy import record_reset_failure, uplink_broken
from broxy4g.services.reset_outcome import ResetOutcome, throttled_outcome
from broxy4g.services.reset_throttle import ResetThrottle
from broxy4g.services.uplink_probe import PublicIpVerdict

logger = logging.getLogger(__name__)


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
        lockout: ResetLockout | None = None,
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
        # Verrou manuel temporaire (en mémoire) : bloque uniquement les resets.
        self.lockout = lockout or ResetLockout()
        self.ip_wait_attempts = ip_wait_attempts
        self.ip_wait_delay = ip_wait_delay

    async def reset(self, dongle_id: UUID, lock_seconds: float | None = None) -> ResetOutcome:
        # Verrou manuel : refus ``LOCK`` avant toute autre garde ; le proxy
        # continue de servir et rien n'est touché (ni throttle, ni matériel).
        key = str(dongle_id)
        if self.lockout.is_locked(key):
            remaining = self.lockout.remaining(key)
            logger.info(
                "reset dongle %s refusé : proxy verrouillé manuellement (%.0fs restants)",
                dongle_id, remaining,
            )
            return ResetOutcome(
                "LOCK",
                f"proxy verrouillé : reset indisponible pendant encore {math.ceil(remaining)}s",
            )
        # Garde anti-tempête : le refus précède le verrou et ne touche pas le matériel.
        if self.throttle is not None:
            reason = self.throttle.reject(key)
            if reason is not None:
                logger.info(
                    "reset dongle %s refusé par la garde anti-tempête (%s)", dongle_id, reason
                )
                return throttled_outcome(reason, self.throttle)
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
            # Le verrou manuel n'est armé qu'à la fin d'un reset réellement DONE
            # (jamais sur un refus, un PENDING ou un ERROR).
            if lock_seconds is not None and lock_seconds > 0:
                self.lockout.lock(key, lock_seconds)
                logger.info("proxy dongle %s verrouillé %.0fs après reset", dongle_id, lock_seconds)
        return outcome

    async def unlock(self, dongle_id: UUID) -> ResetOutcome:
        """Lève le verrou manuel de reset (idempotent) : action admin journalisée.

        Ne touche ni le throttle, ni la fenêtre post-DONE, ni le ``ResetLock``,
        ni le matériel : le proxy continue de servir.
        """
        dongle = await self.dongle_repo.get_by_id(dongle_id)
        if not dongle:
            return ResetOutcome("ERROR", "dongle introuvable")
        self.lockout.unlock(str(dongle_id))
        logger.info("déverrouillage manuel du reset dongle %s (action admin)", dongle_id)
        return ResetOutcome("DONE", "proxy déverrouillé")

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
            probe = await await_public_ip(
                self.connection,
                dongle_id,
                PublicIpVerdict(metrics.public_ip_verdict, metrics.public_ip),
                self.ip_wait_attempts,
                self.ip_wait_delay,
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
            #
            # Marquage ``ERROR`` seulement si l'uplink est cassé (sonde d'IP
            # publique non ``OK``) : un lien sain conserve son état — marquer
            # ``ERROR`` déclencherait une ré-énumération USB destructive
            # (``usb_recovery``) sur un dongle qui fonctionne.
            message = f"{type(exc).__name__}: {exc}"
            mark_error = await uplink_broken(self.connection, dongle_id)
            await record_reset_failure(self.dongle_repo, dongle_id, message, mark_error)
            return ResetOutcome("ERROR", message)

    async def reset_many(self, dongle_ids: list[UUID]) -> list[tuple[UUID, ResetOutcome]]:
        """Reset plusieurs dongles en parallèle. Un reset par dongle, indépendant.

        Déduplique en conservant l'ordre de première apparition.
        Une erreur d'un dongle n'affecte pas les autres."""
        unique_ids = list(dict.fromkeys(dongle_ids))
        outcomes = await asyncio.gather(*(self.reset(did) for did in unique_ids))
        return list(zip(unique_ids, outcomes, strict=True))
