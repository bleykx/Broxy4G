"""Réservations de ports proxy prises en dehors de la base de l'instance.

La base ne connaît que les ports de **cette** instance. Des conteneurs
résiduels d'une autre instance (ou d'un autre service) peuvent occuper des
adresses du pont partagé ``broxy4g-net`` ou des ports publiés sur l'hôte :
sans les réserver, l'allocation choisirait un port dont l'IP (ou le port
publié) est déjà prise et ``docker compose up`` échouerait (« Address already
in use »). Toute logique Docker reste dans les adaptateurs ; ce module ne
fait que croiser leurs observations avec le schéma port↔IP.
"""

from broxy4g.adapters.docker_compose import DockerComposeAdapter
from broxy4g.adapters.docker_network import DockerNetworkAdapter
from broxy4g.domain.proxy_network import (
    DOCKER_NETWORK_NAME,
    PROXY_PORT_END,
    PROXY_PORT_START,
    port_for_ip,
)


class ProxyPortReservations:
    """Ports allouables déjà pris par Docker, indépendamment de la base."""

    def __init__(
        self,
        docker_network: DockerNetworkAdapter,
        docker_compose: DockerComposeAdapter,
        network_name: str = DOCKER_NETWORK_NAME,
    ):
        self._docker_network = docker_network
        self._docker_compose = docker_compose
        self._network_name = network_name

    async def reserved_ports(self) -> set[int]:
        """(IP attachées au pont → ``port_for_ip``) ∪ (ports publiés ∩ plage)."""
        by_ip = {port_for_ip(ip) for ip in await self._docker_network.attached_ips(
            self._network_name
        )}
        by_ip.discard(None)
        published = await self._docker_compose.published_host_ports()
        in_range = {port for port in published if PROXY_PORT_START <= port <= PROXY_PORT_END}
        return by_ip | in_range
