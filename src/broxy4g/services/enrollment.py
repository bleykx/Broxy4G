import logging
from uuid import UUID

from broxy4g.adapters.modem_manager import ModemManagerAdapter
from broxy4g.domain.apn_resolution import ApnResolver
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.settings_repository import AUTO_CREATE_PROXY_KEY, SettingsRepository
from broxy4g.services.connection import ConnectionService
from broxy4g.services.proxy_lifecycle import ProxyLifecycleService

logger = logging.getLogger(__name__)


class EnrollmentService:
    def __init__(
        self,
        dongle_repo: DongleRepository,
        connection_service: ConnectionService,
        proxy_service: ProxyLifecycleService,
        modem: ModemManagerAdapter | None = None,
        apn_source: ApnResolver | None = None,
        settings_repo: SettingsRepository | None = None,
    ):
        self.dongle_repo = dongle_repo
        self.connection_service = connection_service
        self.proxy_service = proxy_service
        self.modem = modem
        self.apn_source = apn_source
        self.settings_repo = settings_repo

    async def enroll(self, identity: IdentityMaterial, label: str, apn: str | None) -> Dongle:
        if not identity.is_complete:
            raise ValueError("Identity material is incomplete")

        existing = await self.dongle_repo.get_by_identity_key(identity.primary_key)
        if existing:
            raise ValueError("already enrolled")

        resolved_apn = apn or await self._resolve_apn(identity)
        dongle = Dongle(identity=identity, label=label, apn=resolved_apn)
        dongle.change_state(DongleState.CONFIGURING)
        await self.dongle_repo.save(dongle)
        await self._maybe_create_proxy(dongle)
        return dongle

    async def _maybe_create_proxy(self, dongle: Dongle) -> None:
        """Crée automatiquement le proxy du dongle si l'option est activée."""
        if self.proxy_service is None:
            return
        if self.settings_repo is not None:
            if not await self.settings_repo.get_bool(AUTO_CREATE_PROXY_KEY, True):
                return
        try:
            await self.proxy_service.create(dongle.id, enabled=True)
        except Exception:
            logger.exception("Création automatique du proxy échouée pour dongle %s", dongle.id)

    async def _resolve_apn(self, identity: IdentityMaterial) -> str | None:
        """Déduit l'APN depuis le modem correspondant à l'identité fournie."""
        if self.apn_source is None or self.modem is None:
            return None
        for modem in await self.modem.list_modems():
            modem_identity = IdentityMaterial(
                imei=modem.imei, usb_serial=modem.usb_serial, iccid=modem.iccid
            )
            if not modem_identity.is_complete:
                continue
            if modem_identity.primary_key != identity.primary_key:
                continue
            suggestion = self.apn_source.resolve(
                modem.imsi, modem.operator_id, modem.operator_name
            )
            return suggestion.apn if suggestion else None
        return None

    async def deactivate(self, dongle_id: UUID) -> None:
        dongle = await self.dongle_repo.get_by_id(dongle_id)
        if not dongle:
            raise ValueError("Dongle not found")

        logger.info("Deactivating dongle %s (%s)", dongle_id, dongle.label)

        try:
            await self.connection_service.disconnect(dongle_id)
        except Exception:
            logger.exception("Failed to disconnect dongle %s during deactivation", dongle_id)

        try:
            proxy = await self.proxy_service.get_by_dongle_id(dongle_id)
            if proxy:
                await self.proxy_service.remove(proxy.id)
        except Exception:
            logger.exception("Failed to remove proxy for dongle %s during deactivation", dongle_id)

        await self.dongle_repo.delete(dongle_id)

    async def update(
        self, dongle_id: UUID, label: str | None, apn: str | None, apn_auth: str | None
    ) -> Dongle:
        dongle = await self.dongle_repo.get_by_id(dongle_id)
        if not dongle:
            raise ValueError("Dongle not found")
        if label is not None:
            dongle.label = label
        if apn is not None:
            dongle.apn = apn
        if apn_auth is not None:
            dongle.apn_auth = apn_auth
        await self.dongle_repo.save(dongle)
        return dongle

    async def get_by_id(self, dongle_id: UUID) -> Dongle | None:
        return await self.dongle_repo.get_by_id(dongle_id)

    async def list_all(self) -> list[Dongle]:
        return await self.dongle_repo.list_all()
