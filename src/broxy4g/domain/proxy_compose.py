"""Rendu du fichier docker-compose d'un proxy 3proxy (une instance par proxy)."""

from dataclasses import dataclass

from .proxy_config import INTERNAL_PROXY_PORT
from .proxy_network import DOCKER_NETWORK_NAME, DOCKER_SUBNET


@dataclass(frozen=True)
class ComposeSpec:
    project_name: str
    container_name: str
    listen_port: int  # port publié sur l'hôte
    cfg_path: str  # chemin absolu du 3proxy.cfg sur l'hôte
    network_ip: str
    image: str = "3proxy/3proxy:latest"
    container_port: int = INTERNAL_PROXY_PORT
    network_name: str = DOCKER_NETWORK_NAME
    subnet: str = DOCKER_SUBNET
    bind_ip: str | None = "0.0.0.0"  # IP d'écoute publiée ; None = conteneur non publié


def render_docker_compose(spec: ComposeSpec) -> str:
    """Rend un docker-compose dédié à un proxy (projet isolé).

    Le bloc ``ports:`` n'est émis que si ``bind_ip`` est défini ; sinon le
    conteneur reste présent (service) mais n'est publié sur aucune interface.

    Le réseau pose ``com.docker.network.bridge.enable_ip_masquerade: "false"`` :
    le masquerade POSTROUTING propre à Docker sur le sous-réseau proxy est
    désactivé, pour que la chaîne nftables ``proxy_nat`` (masquerade conditionné
    à l'interface du dongle lié) reste le seul SNAT légitime.
    """
    lines = [
        f"name: {spec.project_name}",
        "services:",
        "  proxy:",
        f"    image: {spec.image}",
        f"    container_name: {spec.container_name}",
        "    restart: unless-stopped",
    ]
    if spec.bind_ip is not None:
        lines += [
            "    ports:",
            f'      - "{spec.bind_ip}:{spec.listen_port}:{spec.container_port}"',
        ]
    lines += [
        "    volumes:",
        f'      - "{spec.cfg_path}:/etc/3proxy/3proxy.cfg:ro"',
        "    networks:",
        f"      {spec.network_name}:",
        f"        ipv4_address: {spec.network_ip}",
        "networks:",
        f"  {spec.network_name}:",
        f"    name: {spec.network_name}",
        "    driver: bridge",
        "    driver_opts:",
        '      com.docker.network.bridge.enable_ip_masquerade: "false"',
        "    ipam:",
        "      config:",
        f"        - subnet: {spec.subnet}",
    ]
    return "\n".join(lines) + "\n"
