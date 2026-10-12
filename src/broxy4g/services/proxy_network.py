"""Réparation de la passerelle IP du bridge Docker du réseau proxy.

Mode de panne observé : le device bridge de ``broxy4g-net`` perd son IP
passerelle (``172.28.0.1/16``). La table ``main`` n'a alors plus de route
vers ``172.28.0.0/16`` : les paquets DNATés vers les proxys ne sont plus
routables et tombent silencieusement (timeouts clients, aucun RST/ICMP).
Ré-affirmer l'adresse suffit : le noyau réinstalle la route connectée
(``proto kernel``) et la passerelle redevient atteignable.
"""

import asyncio
import logging

from broxy4g.adapters.docker_network import DockerNetworkAdapter
from broxy4g.adapters.net_interface import InterfaceAdapter
from broxy4g.domain.proxy_network import (
    DOCKER_GATEWAY,
    DOCKER_GATEWAY_PREFIX,
    DOCKER_NETWORK_NAME,
)

logger = logging.getLogger(__name__)


class ProxyNetworkService:
    """Vérifie et restaure l'IP passerelle du bridge du réseau proxy.

    Idempotent : adresse déjà présente → aucune écriture système. Ne touche
    que le bridge du réseau proxy (jamais ``flush_ipv4`` ni suppression
    d'adresse) : le bridge porte potentiellement des conteneurs actifs.
    """

    def __init__(
        self,
        docker_network: DockerNetworkAdapter,
        interfaces: InterfaceAdapter,
        network_name: str = DOCKER_NETWORK_NAME,
        gateway: str = DOCKER_GATEWAY,
        prefix: int = DOCKER_GATEWAY_PREFIX,
    ):
        self._docker_network = docker_network
        self._interfaces = interfaces
        self._network_name = network_name
        self.gateway = gateway
        self.prefix = prefix
        self._lock = asyncio.Lock()

    async def ensure_gateway(self) -> bool:
        """Restaure la passerelle du bridge si elle a disparu.

        Retourne ``True`` si une réparation a été effectuée, ``False`` sinon
        (état nominal, ou réseau absent = premier démarrage : ce n'est pas
        une anomalie, on ne tente rien).
        """
        async with self._lock:
            info = await self._docker_network.inspect(self._network_name)
            if info is None:
                logger.debug(
                    "Réseau Docker %s absent : passerelle non vérifiée",
                    self._network_name,
                )
                return False
            current = await self._interfaces.get_ip(info.bridge_device)
            if current == self.gateway:
                return False
            logger.warning(
                "Passerelle %s absente de %s (réseau %s) : réparation",
                self.gateway,
                info.bridge_device,
                self._network_name,
            )
            await self._interfaces.set_up(info.bridge_device)
            await self._interfaces.assign_address(info.bridge_device, self.gateway, self.prefix)
            return True
