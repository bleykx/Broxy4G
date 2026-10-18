"""Réseau proxy Docker partagé : création idempotente et réparation passerelle.

Deux modes de panne observés :
- le réseau ``broxy4g-net`` n'existe plus (reboot, suppression accidentelle) :
  il est recréé avec son sous-réseau, sa passerelle et le masquerade Docker
  désactivé ;
- le device bridge perd son IP passerelle (``172.28.0.1/16``) : la table
  ``main`` n'a plus de route vers ``172.28.0.0/16`` et les paquets DNATés
  vers les proxys tombent silencieusement. Ré-affirmer l'adresse suffit : le
  noyau réinstalle la route connectée (``proto kernel``).

Un pont existant n'est **jamais** supprimé ni recréé : des conteneurs actifs
y vivent.
"""

import asyncio
import logging

from broxy4g.adapters.docker_network import DockerNetworkAdapter
from broxy4g.adapters.net_interface import InterfaceAdapter
from broxy4g.domain.proxy_network import (
    DOCKER_GATEWAY,
    DOCKER_GATEWAY_PREFIX,
    DOCKER_NETWORK_NAME,
    DOCKER_SUBNET,
)

logger = logging.getLogger(__name__)


class ProxyNetworkService:
    """Crée le réseau proxy s'il est absent et répare la passerelle du bridge.

    Idempotent : état nominal → aucune écriture système. Ne touche que le
    bridge du réseau proxy (jamais ``flush_ipv4`` ni suppression d'adresse) :
    le bridge porte potentiellement des conteneurs actifs.
    """

    def __init__(
        self,
        docker_network: DockerNetworkAdapter,
        interfaces: InterfaceAdapter,
        network_name: str = DOCKER_NETWORK_NAME,
        gateway: str = DOCKER_GATEWAY,
        prefix: int = DOCKER_GATEWAY_PREFIX,
        subnet: str = DOCKER_SUBNET,
    ):
        self._docker_network = docker_network
        self._interfaces = interfaces
        self._network_name = network_name
        self.gateway = gateway
        self.prefix = prefix
        self._subnet = subnet
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
            return await self._repair_gateway(info)

    async def ensure_ready(self) -> bool:
        """Vérifie que le réseau proxy est exploitable avant tout conteneur.

        Réseau absent → le crée (``create_network`` idempotent) et journalise
        l'action d'administration ; réseau présent → se réduit à
        ``ensure_gateway``. Retourne ``True`` si une action a été effectuée.
        Un pont existant n'est jamais supprimé ni recréé.
        """
        async with self._lock:
            info = await self._docker_network.inspect(self._network_name)
            if info is None:
                logger.info(
                    "Création du réseau Docker partagé %s "
                    "(subnet %s, passerelle %s)",
                    self._network_name,
                    self._subnet,
                    self.gateway,
                )
                await self._docker_network.create_network(
                    self._network_name, self._subnet, self.gateway
                )
                return True
            return await self._repair_gateway(info)

    async def _repair_gateway(self, info) -> bool:
        """Ré-affirme la passerelle du bridge ; ``False`` si déjà en place.

        À appeler sous ``self._lock`` (``ensure_gateway`` / ``ensure_ready``).
        """
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
