import logging

from broxy4g.adapters.adb import AdbAdapter
from broxy4g.adapters.modem_manager import ModemInfo, ModemManagerAdapter
from broxy4g.domain.apn_resolution import ApnResolver
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.discovery import DiscoveredDongle, DiscoveryService

logger = logging.getLogger(__name__)


class DiscoveryWorker:
    """Détecte les dongles USB et les téléphones ADB non enrôlés.

    La détection n'est journalisée qu'à la transition (première
    observation) : le dédoublonnage des identités annoncées est
    conservé en mémoire et réinitialisé au redémarrage du processus
    (au plus une ligne supplémentaire par appareil au redémarrage).
    """

    def __init__(
        self,
        modem: ModemManagerAdapter,
        dongle_repo: DongleRepository,
        apn_source: ApnResolver | None = None,
        adb: AdbAdapter | None = None,
    ):
        self.modem = modem
        self.dongle_repo = dongle_repo
        self.service = DiscoveryService(modem, dongle_repo, apn_source=apn_source, adb=adb)
        self._announced_keys: set[str] = set()

    @staticmethod
    def _identity_key(dongle: DiscoveredDongle) -> str | None:
        """Clé d'identité pour le dédoublonnage (IMEI > USB > ICCID > ADB)."""
        return (
            dongle.identity_imei
            or dongle.identity_usb_serial
            or dongle.identity_iccid
            or dongle.identity_adb_serial
        )

    async def discover(self) -> list[ModemInfo]:
        """Scanne les modems et les téléphones ADB, retourne les modems non enrôlés."""
        discovered_dongles = await self.service.list_unenrolled()
        for dongle in discovered_dongles:
            identity_key = self._identity_key(dongle)
            if identity_key in self._announced_keys:
                continue
            if dongle.transport == "adb":
                logger.info(
                    "Téléphone ADB détecté : %s (%s)",
                    dongle.suggested_label, dongle.identity_adb_serial,
                )
            else:
                logger.info("Nouveau dongle détecté : %s (%s)", dongle.suggested_label, identity_key)
        self._announced_keys = {
            self._identity_key(d) for d in discovered_dongles
        }
        # Return the original ModemInfo objects for backward compatibility
        modems = await self.modem.list_modems()
        modem_map = {}
        for m in modems:
            identity = IdentityMaterial(imei=m.imei, usb_serial=m.usb_serial, iccid=m.iccid)
            if not identity.is_complete:
                continue
            modem_map[identity.primary_key] = m

        result = []
        for dongle in discovered_dongles:
            identity_key = self._identity_key(dongle)
            if identity_key in modem_map:
                result.append(modem_map[identity_key])
        return result
