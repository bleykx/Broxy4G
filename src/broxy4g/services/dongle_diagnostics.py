"""Sondes de diagnostic d'un dongle : signal, ping, IP publique, session data.

L'identifiant du modem est résolu par identité (IMEI) pour absorber les
changements de port USB ; les mesures ping / IP publique sont épinglées à
l'IP source du bearer (voir ``UplinkProbe``).
"""

from dataclasses import dataclass
from uuid import UUID

from broxy4g.adapters.connectivity import ConnectivityAdapter, PingResult
from broxy4g.adapters.modem_manager import ModemManagerAdapter, SignalQuality
from broxy4g.adapters.net_interface import InterfaceAdapter
from broxy4g.domain.degraded_arbiter import ProbeVerdict
from broxy4g.domain.dongle import Dongle
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.uplink_probe import PublicIpVerdict, UplinkProbe
from broxy4g.services.uplink_routes import UplinkRouteService

__all__ = ["DongleDiagnostics", "DongleMetrics"]


@dataclass
class DongleMetrics:
    signal_quality: SignalQuality | None = None
    ping: PingResult | None = None
    public_ip: str | None = None
    public_ip_verdict: ProbeVerdict | None = None
    proxy_health: str | None = None


class DongleDiagnostics:
    """Mesure l'état d'un dongle (signal, ping, IP publique, session data)."""

    def __init__(
        self,
        dongle_repo: DongleRepository,
        modem: ModemManagerAdapter,
        iface: InterfaceAdapter,
        connectivity: ConnectivityAdapter,
        uplink_routes: UplinkRouteService,
    ):
        self.dongle_repo = dongle_repo
        self.modem = modem
        self.probe = UplinkProbe(iface, connectivity)
        self.uplink_routes = uplink_routes

    async def get_metrics(self, dongle_id: UUID) -> DongleMetrics:
        dongle = await self._load(dongle_id)
        signal = None
        # Resolve modem to handle USB port changes
        modem = await self.uplink_routes.resolve_modem(dongle)
        if modem:
            signal = await self.modem.get_signal(modem.path)
        ping = await self.probe.ping(dongle.interface_name)
        # Une seule sonde d'IP publique : le verdict tri-état porte aussi l'IP
        # (``public_ip`` conserve sa sémantique ``str | None`` inchangée).
        ip_probe = await self.probe.public_ip_verdict(dongle.interface_name)
        return DongleMetrics(
            signal_quality=signal,
            ping=ping,
            public_ip=ip_probe.ip,
            public_ip_verdict=ip_probe.verdict,
        )

    async def get_public_ip(self, dongle_id: UUID) -> str | None:
        dongle = await self._load(dongle_id)
        return await self.probe.public_ip(dongle.interface_name)

    async def get_public_ip_verdict(self, dongle_id: UUID) -> PublicIpVerdict:
        dongle = await self._load(dongle_id)
        return await self.probe.public_ip_verdict(dongle.interface_name)

    async def get_session_id(self, dongle_id: UUID) -> str | None:
        """Identifiant de la session data courante du dongle (None si inconnue)."""
        dongle = await self._load(dongle_id)
        modem = await self.uplink_routes.resolve_modem(dongle)
        if not modem:
            return None
        return await self.modem.get_session_id(modem.path)

    async def _load(self, dongle_id: UUID) -> Dongle:
        dongle = await self.dongle_repo.get_by_id(dongle_id)
        if not dongle:
            raise ValueError("Dongle not found")
        return dongle
