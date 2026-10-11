import logging

from broxy4g.adapters.local_uplink import LocalUplinkAdapter
from broxy4g.domain.proxy_listen import InvalidListenInterface, is_listen_kind_allowed
from broxy4g.repositories.settings_repository import LISTEN_INTERFACE_KEY, SettingsRepository

logger = logging.getLogger(__name__)

DEFAULT_BIND_IP = "0.0.0.0"


class ProxyListenService:
    def __init__(self, settings_repo: SettingsRepository, local_uplink: LocalUplinkAdapter, applier=None):
        self._settings_repo = settings_repo
        self._local_uplink = local_uplink
        self._applier = applier  # ProxyLifecycleService (reapply_all_enabled), fixé après création

    @property
    def applier(self):
        """Référence au service de cycle de vie, fixée après création (anti-cyclicité)."""
        return self._applier

    @applier.setter
    def applier(self, value) -> None:
        self._applier = value

    async def get_interface(self) -> str | None:
        name = await self._settings_repo.get(LISTEN_INTERFACE_KEY)
        return name or None

    async def resolve_bind_ip(self) -> str | None:
        """IP IPv4 de bind pour la publication Docker. None = ne pas publier (interface absente)."""
        name = await self.get_interface()
        if name is None:
            return DEFAULT_BIND_IP
        for iface in await self._local_uplink.list_interfaces():
            if iface.name == name and iface.ip:
                return iface.ip
        logger.warning("Interface d'écoute %s absente ou sans IPv4 ; port proxy non publié", name)
        return None

    async def set_interface(self, name: str | None) -> str | None:
        if not name:
            await self._settings_repo.set(LISTEN_INTERFACE_KEY, "")
            await self._reapply_all()
            return None
        interfaces = {i.name: i for i in await self._local_uplink.list_interfaces()}
        iface = interfaces.get(name)
        if iface is None:
            raise InvalidListenInterface(f"interface inconnue : {name}")
        if not is_listen_kind_allowed(iface.kind):
            raise InvalidListenInterface(
                f"interface d'écoute non autorisée : {name} (type {iface.kind}) ; "
                "seules les interfaces ethernet ou wifi sont acceptées"
            )
        if not iface.ip:
            raise InvalidListenInterface(f"interface {name} sans adresse IPv4")
        await self._settings_repo.set(LISTEN_INTERFACE_KEY, name)
        await self._reapply_all()
        return name

    async def _reapply_all(self) -> None:
        if self._applier is None:
            return
        try:
            await self._applier.reapply_all_enabled()
        except Exception:
            logger.exception("Échec de la réapplication des proxies après changement d'interface d'écoute")
