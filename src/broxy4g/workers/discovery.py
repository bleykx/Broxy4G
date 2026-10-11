import logging

from broxy4g.adapters.modem_manager import ModemInfo, ModemManagerAdapter
from broxy4g.domain.apn_resolution import ApnResolver
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.discovery import DiscoveryService

logger = logging.getLogger(__name__)


class DiscoveryWorker:
    """Détecte les dongles USB via ModemManager et les marque DISCOVERED/UNENROLLED."""

    def __init__(
        self,
        modem: ModemManagerAdapter,
        dongle_repo: DongleRepository,
        apn_source: ApnResolver | None = None,
    ):
        self.modem = modem
        self.dongle_repo = dongle_repo
        self.service = DiscoveryService(modem, dongle_repo, apn_source=apn_source)

    async def discover(self) -> list[ModemInfo]:
        """Scanne les modems et retourne ceux non encore enrôlés."""
        discovered_dongles = await self.service.list_unenrolled()
        for dongle in discovered_dongles:
            logger.info("Nouveau dongle détecté : %s (%s)", dongle.suggested_label, 
                       dongle.identity_imei or dongle.identity_usb_serial or dongle.identity_iccid)
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
            identity_key = dongle.identity_imei or dongle.identity_usb_serial or dongle.identity_iccid
            if identity_key in modem_map:
                result.append(modem_map[identity_key])
        return result
