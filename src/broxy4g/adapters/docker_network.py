"""Inspection des réseaux Docker et découverte du device bridge d'hôte.

Sans le nom du device bridge (``br-<12 premiers hex de l'id>`` ou l'option
``com.docker.network.bridge.name``), il est impossible de vérifier ou de
restaurer l'IP passerelle du réseau proxy sur l'hôte : c'est ce device qui
porte ``172.28.0.1`` ; sans elle, les paquets DNATés vers les proxys ne sont
plus routables (drop silencieux).
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .shell import ShellAdapter

# Un seul appel inspect, format pipe : name|id|driver|bridge_opt|subnet|gateway…
_INSPECT_FORMAT = (
    "{{.Name}}|{{.ID}}|{{.Driver}}|"
    '{{index .Options "com.docker.network.bridge.name"}}|'
    "{{range .IPAM.Config}}{{.Subnet}}|{{.Gateway}}{{end}}"
)


@dataclass(frozen=True)
class DockerNetworkInfo:
    name: str
    id: str
    driver: str
    bridge_device: str
    subnet: str | None
    gateway: str | None


@runtime_checkable
class DockerNetworkAdapter(Protocol):
    async def inspect(self, name: str) -> DockerNetworkInfo | None: ...


class RealDockerNetwork:
    """Inspection réelle via ``docker network inspect``."""

    def __init__(self, shell: ShellAdapter):
        self.shell = shell

    async def inspect(self, name: str) -> DockerNetworkInfo | None:
        """Renvoie les infos du réseau, ou ``None`` s'il n'existe pas.

        ``None`` couvre aussi docker indisponible (rc != 0) : l'appelant doit
        alors se taire (premier démarrage, daemon pas encore prêt).
        """
        result = await self.shell.run(["docker", "network", "inspect", name, "--format", _INSPECT_FORMAT])
        if result.returncode != 0:
            return None
        return self._parse(name, result.stdout)

    @staticmethod
    def _parse(name: str, stdout: str) -> DockerNetworkInfo | None:
        lines = stdout.strip().splitlines()
        if not lines:
            return None
        parts = lines[0].split("|", 4)
        if len(parts) < 5:
            return None
        parsed_name, network_id, driver, bridge_opt, ipam = parts
        # Un seul IPAM Config est attendu ; on ne lit que le premier couple.
        subnet, _, gateway = ipam.partition("|")
        bridge_device = bridge_opt.strip() or f"br-{network_id[:12]}"
        return DockerNetworkInfo(
            name=parsed_name or name,
            id=network_id,
            driver=driver,
            bridge_device=bridge_device,
            subnet=subnet or None,
            gateway=gateway or None,
        )


class FakeDockerNetwork:
    """Réseau Docker simulé (aucun appel système) pour les tests."""

    def __init__(self):
        self._networks: dict[str, DockerNetworkInfo] = {}
        self.calls: list[str] = []

    def set_network(self, info: DockerNetworkInfo) -> None:
        self._networks[info.name] = info

    def remove(self, name: str) -> None:
        self._networks.pop(name, None)

    async def inspect(self, name: str) -> DockerNetworkInfo | None:
        self.calls.append(name)
        return self._networks.get(name)
