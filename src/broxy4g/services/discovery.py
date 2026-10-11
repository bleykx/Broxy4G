from dataclasses import dataclass

from broxy4g.adapters.modem_manager import ModemInfo, ModemManagerAdapter
from broxy4g.domain.apn_resolution import ApnResolver
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.repositories.dongle_repository import DongleRepository


@dataclass
class DiscoveredDongle:
    identity_imei: str | None
    identity_usb_serial: str | None
    identity_iccid: str | None
    interface_name: str | None
    suggested_label: str
    suggested_apn: str | None = None


class DiscoveryService:
    def __init__(
        self,
        modem: ModemManagerAdapter,
        dongle_repo: DongleRepository,
        apn_source: ApnResolver | None = None,
    ):
        self.modem = modem
        self.dongle_repo = dongle_repo
        self.apn_source = apn_source

    async def list_unenrolled(self) -> list[DiscoveredDongle]:
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