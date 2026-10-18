"""Inspection et création idempotente du réseau Docker proxy.

Sans le nom du device bridge (``br-<12 premiers hex de l'id>`` ou l'option
``com.docker.network.bridge.name``), il est impossible de vérifier ou de
restaurer l'IP passerelle du réseau proxy sur l'hôte : c'est ce device qui
porte ``172.28.0.1`` ; sans elle, les paquets DNATés vers les proxys ne sont
plus routables (drop silencieux).

Le pont ``broxy4g-net`` est partagé par tous les conteneurs proxy et créé
**par l'application** (pas par Compose, qui le déclare ``external: true``) :
``create_network`` est idempotent et ne touche jamais un réseau existant.
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
# IP IPv4 des conteneurs attachés, une par conteneur, préfixe CIDR inclus
# (ex. "172.28.0.101/16 172.28.0.102/16 ").
_ATTACHED_IPS_FORMAT = "{{range .Containers}}{{.IPv4Address}} {{end}}"
# Le masquerade POSTROUTING propre à Docker est désactivé : la chaîne nftables
# ``proxy_nat`` doit rester le seul SNAT légitime sur le sous-réseau proxy.
_NO_MASQUERADE_OPT = "com.docker.network.bridge.enable_ip_masquerade=false"


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

    async def create_network(self, name: str, subnet: str, gateway: str) -> None: ...

    async def attached_ips(self, name: str) -> set[str]: ...


class RealDockerNetwork:
    """Inspection et création réelles via ``docker network``."""

    def __init__(self, shell: ShellAdapter):
        self.shell = shell

    async def create_network(self, name: str, subnet: str, gateway: str) -> None:
        """Crée le pont idempotement (bridge, sous-réseau, passerelle,
        masquerade Docker désactivé).

        « already exists » → succès silencieux ; toute autre erreur est
        remontée. Un réseau existant n'est jamais modifié ni recréé.
        """
        result = await self.shell.run(
            [
                "docker", "network", "create",
                "--driver", "bridge",
                "--subnet", subnet,
                "--gateway", gateway,
                "--opt", _NO_MASQUERADE_OPT,
                name,
            ]
        )
        if result.returncode == 0 or "already exists" in result.stderr:
            return
        raise RuntimeError(
            f"docker network create failed (rc={result.returncode}): "
            f"{result.stderr or result.stdout}"
        )

    async def attached_ips(self, name: str) -> set[str]:
        """IP IPv4 (sans préfixe) des conteneurs attachés au réseau.

        Un seul appel ``docker network inspect --format``, borné ; réseau
        absent ou docker indisponible (rc != 0) → ensemble vide.
        """
        result = await self.shell.run(
            ["docker", "network", "inspect", name, "--format", _ATTACHED_IPS_FORMAT],
            timeout=10.0,
        )
        if result.returncode != 0:
            return set()
        return {ip.partition("/")[0] for ip in result.stdout.split()}

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
        self.created: list[str] = []
        self._attached: dict[str, set[str]] = {}

    def set_network(self, info: DockerNetworkInfo) -> None:
        self._networks[info.name] = info

    def remove(self, name: str) -> None:
        self._networks.pop(name, None)

    def set_attached_ips(self, name: str, ips: set[str]) -> None:
        self._attached[name] = set(ips)

    async def inspect(self, name: str) -> DockerNetworkInfo | None:
        self.calls.append(name)
        return self._networks.get(name)

    async def create_network(self, name: str, subnet: str, gateway: str) -> None:
        """Enregistre la création et rend le réseau inspectable (idempotent)."""
        self.created.append(name)
        self._networks.setdefault(
            name,
            DockerNetworkInfo(
                name=name,
                id="00000000000000000000",
                driver="bridge",
                bridge_device="br-000000000000",
                subnet=subnet,
                gateway=gateway,
            ),
        )

    async def attached_ips(self, name: str) -> set[str]:
        return set(self._attached.get(name, set()))
