"""Récupération douce et ré-épinglage d'interface des téléphones ADB.

Extrait de ``workers/reconciliation.py`` : le dongle ADB ne subit JAMAIS de
ré-énumération USB (le netdev RNDIS est instable, pas le bus) ; la récupération
douce se limite à un ``wait_for_device`` borné, et l'interface est ré-épinglee
via sysfs par le service d'uplink smartphone.

Cas matériel prouvé (reboot) : le serveur adb est démarré par l'application au
boot, avant l'énumération USB du téléphone, et reste ensuite aveugle
(``adb devices`` vide) alors que l'interface ADB est bien présente dans sysfs.
Un serveur neuf, démarré après l'énumération, voit immédiatement le device. La
récupération ADB est donc **attendue bornée puis escaladée d'une seule relance
bornée du serveur adb** (cooldown en mémoire par serial pour éviter une tempête
de relances à chaque cycle). Aucune exception ne doit s'échapper de ces
méthodes : le cycle de reconciliation ne doit pas casser.
"""

import logging
import time
from collections.abc import Callable

from broxy4g.adapters.adb import AdbAdapter
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.transport import Transport
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.smartphone_uplink import SmartphoneUplinkService
from broxy4g.services.usb_recovery import UsbRecoveryService

logger = logging.getLogger(__name__)


class AdbSoftRecovery:
    """Récupération douce (modem : ré-énumération USB, ADB : attente bornée puis
    relance bornée du serveur adb) et ré-épinglage du netdev RNDIS d'un téléphone ADB."""

    def __init__(
        self,
        adb: AdbAdapter | None,
        smartphone: SmartphoneUplinkService | None,
        dongle_repo: DongleRepository,
        usb_recovery: UsbRecoveryService | None = None,
        restart_cooldown: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.adb = adb
        self.smartphone = smartphone
        self.dongle_repo = dongle_repo
        self.usb_recovery = usb_recovery
        self._restart_cooldown = restart_cooldown
        self._clock = clock
        self._last_restart: dict[str, float] = {}

    async def attempt_soft_recovery(self, dongle) -> None:
        """Récupération douce (modem : ré-énumération USB, ADB : attente bornée)."""
        if dongle.transport is Transport.ADB:
            return await self._soft_recovery_adb(dongle)
        if self.usb_recovery is None:
            return
        try:
            await self.usb_recovery.attempt(dongle.id)
        except Exception:
            logger.exception("Échec récupération USB douce dongle %s", dongle.id)

    async def _soft_recovery_adb(self, dongle) -> None:
        """Récupération douce ADB : ``wait_for_device`` borné, jamais de ré-énumération
        USB. Si l'attente échoue, une **seule** relance bornée du serveur adb est
        escaladée (cooldown par serial) avant de conclure à l'échec."""
        if self.adb is None:
            logger.debug("Récupération ADB dongle %s sautée : adaptateur ADB non configuré", dongle.id)
            return
        try:
            serial = dongle.identity.adb_serial or dongle.identity.primary_key
        except ValueError:
            logger.warning("Récupération ADB dongle %s sautée : identité ADB introuvable", dongle.id)
            return
        try:
            if await self.adb.wait_for_device(serial):
                return
            await self._escalate_restart(serial, dongle.id)
        except Exception:
            logger.exception("Échec récupération ADB douce dongle %s", dongle.id)

    async def _escalate_restart(self, serial: str, dongle_id: str) -> None:
        """Échec d'attente : relance **une fois** le serveur adb (cooldown par serial),
        puis retente l'attente. Le WARNING d'échec n'est logué qu'après l'échec de la
        tentative escaladée. Le cooldown empêche une tempête de relances à chaque cycle
        de reconciliation sur un device réellement absent."""
        if self._in_restart_cooldown(serial):
            logger.warning(
                "Récupération ADB dongle %s : device %s non revenu (relance en cooldown)",
                dongle_id,
                serial,
            )
            return
        self._last_restart[serial] = self._clock()
        logger.info(
            "ACTION ADMIN : relance du serveur adb pour dongle %s (device %s non revenu)",
            dongle_id,
            serial,
        )
        restarted = await self.adb.restart_server()
        if restarted and await self.adb.wait_for_device(serial):
            logger.info(
                "Récupération ADB dongle %s : device %s revenu après relance du serveur",
                dongle_id,
                serial,
            )
            return
        logger.warning(
            "Récupération ADB dongle %s : device %s non revenu (relance du serveur adb %s)",
            dongle_id,
            serial,
            "réussie" if restarted else "échouée",
        )

    def _in_restart_cooldown(self, serial: str) -> bool:
        """Le dernier serveur relancé pour ce serial est encore dans le cooldown."""
        last = self._last_restart.get(serial)
        if last is None:
            return False
        if self._clock() - last >= self._restart_cooldown:
            del self._last_restart[serial]
            return False
        return True

    async def refresh_interface(self, dongle: Dongle) -> Dongle:
        """Ré-épingle le netdev RNDIS (instable) d'un téléphone ADB via sysfs ; retourne le dongle."""
        if dongle.transport is not Transport.ADB or self.smartphone is None:
            return dongle
        try:
            serial = dongle.identity.adb_serial or dongle.identity.primary_key
        except ValueError:
            return dongle
        resolved = self.smartphone.resolve_interface(serial)
        if resolved and resolved != dongle.interface_name:
            logger.warning(
                "ACTION ADMIN : interface ADB dongle %s %s -> %s (netdev RNDIS)",
                dongle.id,
                dongle.interface_name,
                resolved,
            )
            dongle.interface_name = resolved
            await self.dongle_repo.save(dongle)
        return dongle
