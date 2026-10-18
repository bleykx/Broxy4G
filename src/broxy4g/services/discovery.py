import logging
from dataclasses import dataclass

from broxy4g.adapters.adb import AdbAdapter, AdbDevice
from broxy4g.adapters.modem_manager import ModemInfo, ModemManagerAdapter
from broxy4g.domain.apn_resolution import ApnResolver
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.repositories.dongle_repository import DongleRepository

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredDongle:
    identity_imei: str | None
    identity_usb_serial: str | None
    identity_iccid: str | None
    interface_name: str | None
    suggested_label: str
    suggested_apn: str | None = None
    transport: str = "modem"
    identity_adb_serial: str | None = None
    model: str | None = None


class DiscoveryService:
    def __init__(
        self,
        modem: ModemManagerAdapter,
        dongle_repo: DongleRepository,
        apn_source: ApnResolver | None = None,
        adb: AdbAdapter | None = None,
    ):
        self.modem = modem
        self.dongle_repo = dongle_repo
        self.apn_source = apn_source
        self.adb = adb

    async def list_unenrolled(self) -> list[DiscoveredDongle]:
        discovered_dongles = []
        discovered_dongles.extend(await self._discover_modems())
        discovered_dongles.extend(await self._discover_phones())
        return discovered_dongles

    async def _discover_modems(self) -> list[DiscoveredDongle]:
        discovered_dongles = []
        modems = await self.modem.list_modems()
        for modem in modems:
            identity = IdentityMaterial(
                imei=modem.imei,
                usb_serial=modem.usb_serial,
                iccid=modem.iccid
            )
            if not identity.is_complete:
                continue
            if await self.dongle_repo.get_by_identity_key(identity.primary_key):
                continue
            suggested_label = self._build_suggested_label(identity)
            discovered_dongles.append(DiscoveredDongle(
                identity_imei=identity.imei,
                identity_usb_serial=identity.usb_serial,
                identity_iccid=identity.iccid,
                interface_name=modem.interface_name,
                suggested_label=suggested_label,
                suggested_apn=self._suggest_apn(modem),
            ))
        return discovered_dongles

    async def _discover_phones(self) -> list[DiscoveredDongle]:
        """Détecte les téléphones Android via ADB (état ``device`` uniquement).

        Le serveur ADB peut être absent : un échec est journalisé et aucun
        téléphone n'est retourné, sans propager l'exception.
        """
        if self.adb is None:
            return []
        try:
            devices = await self.adb.list_devices()
        except Exception:
            logger.warning("Détection ADB impossible (serveur indisponible) : 0 téléphone")
            return []
        discovered: list[DiscoveredDongle] = []
        for device in devices:
            if device.state != "device":
                continue
            identity = IdentityMaterial(adb_serial=device.serial)
            if await self.dongle_repo.get_by_identity_key(identity.primary_key):
                continue
            model = await self._phone_model(device)
            label = f"Téléphone {model}" if model else f"Téléphone {device.serial}"
            discovered.append(DiscoveredDongle(
                transport="adb",
                identity_imei=None,
                identity_usb_serial=None,
                identity_iccid=None,
                identity_adb_serial=device.serial,
                interface_name=None,
                suggested_label=label,
                suggested_apn=None,
                model=model,
            ))
        return discovered

    async def _phone_model(self, device: AdbDevice) -> str | None:
        """Résout le modèle affiché : champ ADB d'abord, sinon ``ro.product.model``."""
        if device.model is not None:
            return device.model
        try:
            return await self.adb.get_prop(device.serial, "ro.product.model")
        except Exception:
            logger.warning("Lecture de ro.product.model impossible pour %s", device.serial)
            return None

    def _suggest_apn(self, modem: ModemInfo) -> str | None:
        if self.apn_source is None:
            return None
        suggestion = self.apn_source.resolve(modem.imsi, modem.operator_id, modem.operator_name)
        return suggestion.apn if suggestion else None

    def _build_suggested_label(self, identity: IdentityMaterial) -> str:
        # Use last 4 chars of IMEI > USB serial > ICCID
        value = identity.imei or identity.usb_serial or identity.iccid
        if value:
            return f"Dongle {value[-4:]}"
        return "Dongle"
