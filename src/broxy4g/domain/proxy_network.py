"""Réseau Docker partagé et adressage IP déterministe des proxys.

Tous les proxys vivent sur un même bridge Docker (`broxy4g-net`). L'adresse IP
interne de chaque conteneur est dérivée de son port publié, ce qui garantit
l'unicité sans état supplémentaire et reste stable après un reboot.
"""

PROXY_PORT_START = 31201
PROXY_PORT_END = 31299

DOCKER_NETWORK_NAME = "broxy4g-net"
DOCKER_SUBNET = "172.28.0.0/16"
DOCKER_GATEWAY = "172.28.0.1"
# Préfixe porté par le bridge Docker (172.28.0.1/16), utilisé pour ré-affirmer
# la passerelle si elle a été perdue.
DOCKER_GATEWAY_PREFIX = 16

_FIRST_HOST_OCTET = 101


def network_ip_for_port(port: int) -> str:
    """Adresse IPv4 interne déterministe d'un proxy dans le réseau partagé.

    31201 -> 172.28.0.101 ... 31299 -> 172.28.0.199. Lève ValueError si le
    port est hors de la plage allouable.
    """
    if not (PROXY_PORT_START <= port <= PROXY_PORT_END):
        raise ValueError(
            f"Port {port} hors plage proxy [{PROXY_PORT_START}, {PROXY_PORT_END}]"
        )
    return f"172.28.0.{_FIRST_HOST_OCTET + (port - PROXY_PORT_START)}"
