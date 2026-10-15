"""Récupération douce : ré-énumération USB bornée des dongles bloqués.

Un dongle en ``ERROR``/``OFFLINE`` avec un bearer PPP (port ``net`` absent
chez ModemManager, donc aucun netdev) ne peut pas se rétablir en
reconnectant : chaque cycle de reconciliation échoue de nouveau. Ce service
désautorise puis réautorise le périphérique USB (``authorized`` 0 → 1 dans
sysfs), ce qui force le noyau à le ré-énumérer proprement, sans reboot ni
reset applicatif.

Politique (règles métier) :
- seulement les dongles **enrôlés** en état ``ERROR`` ou ``OFFLINE`` (jamais
  depuis ``RESETTING``, ``CONFIGURING``, ``DISCOVERED``, ``UNENROLLED``,
  ``CONNECTING``, ``ONLINE``, ``DEGRADED``) ;
- jamais si le **verrou de reset** est tenu pour ce dongle ;
- **au plus une tentative par dongle et par cycle de reconciliation** (le
  compteur est remis à zéro par ``begin_cycle()`` au début de
  ``reconcile_all()``) ;
- **cooldown inter-cycles** : après une tentative, la ré-énumération d'un même
  dongle n'est retentée qu'au bout de ``cooldown_seconds`` (300 s par défaut,
  variable ``BROXY4G_USB_RECOVERY_COOLDOWN_SECONDS``). Ce bornage survit aux
  cycles — ``begin_cycle()`` ne le remet **pas** à zéro — et n'est gardé qu'en
  mémoire : un redémarrage du processus autorise donc une tentative immédiate ;
- **attente bornée** de la réapparition du modem chez ModemManager
  (disparition puis retour), timeout configurable : au-delà, un WARNING est
  journalisé et c'est le cycle suivant qui décide — jamais de boucle ;
- **idempotence** : aucune écriture ne réécrit une valeur déjà présente
  (pas de ``1`` sur un fichier déjà à ``1``) ; le toggle ``0→1`` reste
  l'action utile même si le device est déjà ``authorized=1`` (c'est le cas
  d'un dongle branché bloqué), et une tentative ne laisse jamais
  ``authorized`` à ``0`` sans le signaler (échec persistant journalisé) ;
- chemin USB introuvable ⇒ no-op journalisé en WARNING, jamais d'exception.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from uuid import UUID

from broxy4g.adapters.usb_device import UsbDeviceAdapter
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.reset_states import ResetLock
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.uplink_routes import UplinkRouteService

logger = logging.getLogger(__name__)

# États éligibles : enrôlés seulement (DISCOVERED/UNENROLLED exclus d'office).
ELIGIBLE_STATES = frozenset({DongleState.ERROR, DongleState.OFFLINE})


@dataclass
class UsbRecoveryResult:
    attempted: bool  # le toggle authorized 0→1 a été effectué
    reappeared: bool  # modem revenu chez ModemManager après la ré-énumération
    reason: str | None = None


class UsbRecoveryService:
    """Ré-énumération USB bornée : une fois par cycle, avec backoff inter-cycles."""

    def __init__(
        self,
        dongle_repo: DongleRepository,
        uplink_routes: UplinkRouteService,
        usb: UsbDeviceAdapter,
        reset_lock: ResetLock | None = None,
        reappearance_timeout: float = 10.0,
        poll_delay: float = 0.5,
        cooldown_seconds: float = 300.0,
    ):
        self.dongle_repo = dongle_repo
        self.uplink_routes = uplink_routes
        self.usb = usb
        self.reset_lock = reset_lock
        self._reappearance_timeout = reappearance_timeout
        self._poll_delay = poll_delay
        self._cooldown_seconds = cooldown_seconds
        # Horodatage monotone de la dernière tentative par dongle ; survit aux
        # cycles (jamais remis à zéro par ``begin_cycle``), mémoire seulement.
        self._last_attempt_at: dict[UUID, float] = {}
        self._attempted_this_cycle: set[UUID] = set()

    def begin_cycle(self) -> None:
        """Début de ``reconcile_all()`` : réinitialise le compteur par cycle.

        Ne remet **pas** à zéro le cooldown inter-cycles (``_last_attempt_at``) :
        celui-ci est volontairement conservé d'un cycle à l'autre pour éviter le
        flottement de ré-énumération d'un dongle resté bloqué.
        """
        self._attempted_this_cycle.clear()

    async def attempt(self, dongle_id: UUID) -> UsbRecoveryResult:
        dongle = await self.dongle_repo.get_by_id(dongle_id)
        if dongle is None:
            return UsbRecoveryResult(False, False, "dongle introuvable")
        if dongle.state not in ELIGIBLE_STATES:
            return UsbRecoveryResult(False, False, f"état {dongle.state.value} inéligible")
        if self.reset_lock is not None and self.reset_lock.is_locked(str(dongle_id)):
            return UsbRecoveryResult(False, False, "reset en cours")
        if dongle_id in self._attempted_this_cycle:
            return UsbRecoveryResult(False, False, "déjà tenté ce cycle")
        cooldown_reason = self._cooldown_reason(dongle_id)
        if cooldown_reason is not None:
            logger.info(
                "Récupération USB dongle %s reportée (cooldown inter-cycles) : %s",
                dongle_id, cooldown_reason,
            )
            return UsbRecoveryResult(False, False, cooldown_reason)
        self._attempted_this_cycle.add(dongle_id)
        # Horodater l'essai maintenant : le cooldown tient aussi si la tentative
        # échoue (c'est précisément le but — pas de ré-énumération à chaque cycle).
        self._last_attempt_at[dongle_id] = time.monotonic()
        return await self._reenumerate(dongle)

    def _cooldown_reason(self, dongle_id: UUID) -> str | None:
        """Raison de blocage par cooldown (secondes restantes), ou None si autorisé.

        Seule la dernière **tentative réelle** est horodatée ; un blocage par
        cooldown n'actualise jamais l'horodatage.
        """
        last = self._last_attempt_at.get(dongle_id)
        if last is None:
            return None
        elapsed = time.monotonic() - last
        if elapsed < self._cooldown_seconds:
            remaining = self._cooldown_seconds - elapsed
            return f"cooldown actif ({remaining:.0f}s restantes)"
        return None

    async def _reenumerate(self, dongle: Dongle) -> UsbRecoveryResult:
        authorized_file = await self._resolve_authorized_file(dongle)
        if authorized_file is None:
            logger.warning(
                "Récupération USB dongle %s non tentée : périphérique USB introuvable "
                "dans sysfs (ModemManager ne rapporte pas de chemin device exploitable)",
                dongle.id,
            )
            return UsbRecoveryResult(False, False, "périphérique USB introuvable")
        if not await self._toggle_authorized(authorized_file, dongle.id):
            return UsbRecoveryResult(False, False, "échec désautorisation")
        if not await self._ensure_reauthorized(authorized_file, dongle.id):
            return UsbRecoveryResult(True, False, "échec ré-autorisation")
        reappeared = await self._wait_reevaluation(dongle)
        if not reappeared:
            logger.warning(
                "Ré-énumération USB dongle %s : le modem n'est pas réapparu après %.1fs — "
                "le cycle suivant décidera",
                dongle.id, self._reappearance_timeout,
            )
        return UsbRecoveryResult(True, reappeared)

    async def _toggle_authorized(self, authorized_file: str, dongle_id: UUID) -> bool:
        """Désautorise le device (``authorized=0``) — le premier pas du toggle.

        Idempotence par étape : pas d'écriture si le fichier est déjà à ``0``.
        Un échec laisse le device inchangé (souvent ``1``) et retourne False.
        """
        return await self._write_if_different(authorized_file, False, dongle_id)

    async def _ensure_reauthorized(self, authorized_file: str, dongle_id: UUID) -> bool:
        """Réautorise le device ; ``authorized`` doit finir à ``1``.

        Idempotence par étape : pas d'écriture si le fichier est déjà à ``1``
        (un autre agent a réautorisé entre les deux). Un échec est retenté
        une fois ; un échec persistant est journalisé et le device reste à
        ``0`` — le cycle suivant réessaiera, rien n'est masqué.
        """
        if await self._write_if_different(authorized_file, True, dongle_id):
            return True
        # L'exception d'écriture est déjà journalisée par _write_if_different.
        logger.warning(
            "Ré-autorisation échouée (authorized=1) dongle %s, nouvelle tentative",
            dongle_id,
        )
        if await self._write_if_different(authorized_file, True, dongle_id):
            return True
        logger.warning(
            "Dongle %s : authorized laissé à 0, ré-autorisation impossible — "
            "le cycle suivant réessaiera",
            dongle_id,
        )
        return False

    async def _write_if_different(
        self, authorized_file: str, value: bool, dongle_id: UUID
    ) -> bool:
        """Écrit ``value`` seulement si le fichier ne le contient pas déjà."""
        try:
            if await self.usb.read_authorized(authorized_file) == value:
                return True
            await self.usb.write_authorized(authorized_file, value)
            return True
        except Exception:
            logger.exception(
                "Écriture authorized=%d échouée pour dongle %s",
                int(value), dongle_id,
            )
            return False

    async def _resolve_authorized_file(self, dongle: Dongle) -> str | None:
        modem = await self.uplink_routes.resolve_modem(dongle)
        if modem is None or not modem.device_path:
            return None
        return await self.usb.resolve_authorized_file(modem.device_path)

    async def _wait_reevaluation(self, dongle: Dongle) -> bool:
        """Attend, de façon bornée, la disparition puis le retour du modem.

        La ré-énumération n'est validée que si le modem a effectivement
        disparu puis réapparu chez ModemManager avant le timeout.
        """
        deadline = time.monotonic() + self._reappearance_timeout
        seen_gone = False
        while time.monotonic() < deadline:
            present = await self.uplink_routes.resolve_modem(dongle) is not None
            if seen_gone and present:
                return True
            seen_gone = not present
            await asyncio.sleep(self._poll_delay)
        return False
