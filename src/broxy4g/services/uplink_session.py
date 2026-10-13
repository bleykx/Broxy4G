"""Cycle de vie de l'uplink d'un dongle : teardown « barrier-first » et attentes.

Plancher anti-fuite : la table de routage dédiée ne doit JAMAIS rester sans
route ``default`` ; ``add_barrier_route`` (``unreachable default``) est posé
AVANT toute coupure. Teardown = ``add_barrier_route`` → coupure modem/iface
→ ``remove_source_rule``. Aucun ``remove_rule``, aucun ``remove_route`` :
la règle ``ip rule fwmark`` du proxy et le slot
(``route_table_id``/``fwmark``) sont CONSERVÉS ; seule la règle source
``from <ip>`` disparaît (IP bearer périmée). Sans ``default`` utilisable,
aucun repli sur ``main`` : le proxy du dongle n'a AUCUN réseau tant que
l'uplink n'est pas rétabli (aucune fuite par eth).
"""

import asyncio

from broxy4g.adapters.modem_manager import ModemManagerAdapter
from broxy4g.adapters.net_interface import InterfaceAdapter
from broxy4g.adapters.routing import RoutingAdapter
from broxy4g.domain.dongle import Dongle
from broxy4g.services.uplink_routes import UplinkRouteService

__all__ = ["UplinkSession"]


class UplinkSession:
    """Gère la coupure de l'uplink et les attentes de (ré)établissement."""

    def __init__(
        self,
        modem: ModemManagerAdapter,
        iface: InterfaceAdapter,
        routing: RoutingAdapter,
        uplink_routes: UplinkRouteService,
        ip_wait_attempts: int = 10,
        ip_wait_delay: float = 2.0,
        modem_wait_attempts: int = 60,
        modem_wait_delay: float = 1.5,
    ):
        self.modem = modem
        self.iface = iface
        self.routing = routing
        self.uplink_routes = uplink_routes
        self._ip_wait_attempts = ip_wait_attempts
        self._ip_wait_delay = ip_wait_delay
        self._modem_wait_attempts = modem_wait_attempts
        self._modem_wait_delay = modem_wait_delay

    async def teardown(self, dongle: Dongle) -> None:
        """Déconnecte le bearer et nettoie l'interface (« barrier-first »).

        Ne change pas l'état, ne sauvegarde pas, n'applique pas l'isolation
        (partagé disconnect/reset).
        """
        # Étape 1 : plancher anti-fuite AVANT toute coupure.
        if dongle.route_table_id:
            await self.routing.add_barrier_route(dongle.route_table_id)
        # Étape 2 : coupure du bearer et de l'interface.
        modem = await self.uplink_routes.resolve_modem(dongle)  # par identité (USB port change)
        if modem and modem.interface_name:
            await self.modem.disconnect(modem.path)
            await self.iface.flush_ipv4(modem.interface_name)
            await self.iface.set_down(modem.interface_name)
        elif dongle.interface_name:
            # Try to clean up even if modem is not found
            await self.iface.flush_ipv4(dongle.interface_name)
            await self.iface.set_down(dongle.interface_name)
        # Étape 3 : l'IP bearer disparaît, la règle source est obsolète.
        if dongle.route_table_id:
            await self.routing.remove_source_rule(dongle.route_table_id)

    async def wait_for_ip(self, interface: str, expected: str | None = None) -> str:
        """Poll iface.get_ip until the expected source IP appears or attempts run out.

        ``expected`` (l'IP du bearer) est exigée : une adresse résiduelle, même
        présente en 1re position, ne doit pas valider la connexion.
        """
        for _ in range(self._ip_wait_attempts):
            ip = await self.iface.get_ip(interface)
            if ip and (expected is None or ip == expected):
                return ip
            await asyncio.sleep(self._ip_wait_delay)
        raise RuntimeError(
            f"no IPv4 on {interface} after {self._ip_wait_attempts} attempts",
        )

    async def wait_reenumeration(self, dongle: Dongle) -> None:
        """Attend la ré-énumération USB après ``reset_modem``.

        Phase 1 : disparition du modem de ModemManager (~15-40 s).
        Phase 2 : réapparition avec une interface exploitable, retrouvée
        par identité (IMEI), parfois sous un nouveau chemin.
        """
        for _ in range(self._modem_wait_attempts):
            if await self.uplink_routes.resolve_modem(dongle) is None:
                break
            await asyncio.sleep(self._modem_wait_delay)
        for _ in range(self._modem_wait_attempts):
            candidate = await self.uplink_routes.resolve_modem(dongle)
            if candidate is not None and candidate.interface_name:
                break
            await asyncio.sleep(self._modem_wait_delay)
        else:
            raise RuntimeError("modem non réapparu après reset")
