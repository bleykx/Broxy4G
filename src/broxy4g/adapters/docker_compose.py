"""Adaptateur dédié à la CLI ``docker compose`` (un projet par proxy).

Distinct de :mod:`broxy4g.adapters.docker` (gestion de conteneurs unitaires).
"""

import asyncio
import logging
import os
import re
from typing import Protocol, runtime_checkable

from .shell import ShellAdapter

logger = logging.getLogger(__name__)

_PROJECT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")

# Verrou de processus (single-flight) : sérialise up/down/restart/remove pour
# TOUTES les instances. Deux `docker compose up` du même projet ne peuvent
# plus s'entrelacer (conflit "container name ... already in use" entre un
# reset et la boucle de réconciliation).
_COMPOSE_OP_LOCK = asyncio.Lock()


@runtime_checkable
class DockerComposeAdapter(Protocol):
    async def up(self, project: str, compose_dir: str) -> None:
        ...

    async def down(self, project: str, compose_dir: str) -> None:
        ...

    async def restart(self, project: str, compose_dir: str) -> None:
        ...

    async def state(self, project: str) -> str | None:
        ...

    async def remove(self, project: str, compose_dir: str) -> None:
        ...

    async def published_host_ports(self) -> set[int]:
        ...


def parse_published_ports(stdout: str) -> set[int]:
    """Ports hôte publiés extraits du format ``{{.Ports}}`` de ``docker ps``.

    Lignes séparées par des retours à la ligne, entrées séparées par des
    virgules : ``0.0.0.0:31201->3128/tcp, 172.28.0.2:80->80/tcp, 9000/tcp``.
    Seuls les ports **hôte** (avant ``->``) sont retenus ; les ports internes
    non publiés (``9000/tcp``) sont ignorés.
    """
    ports: set[int] = set()
    for entry in stdout.replace("\n", ",").split(","):
        host = entry.strip().split("->", 1)[0]
        _, sep, candidate = host.rpartition(":")
        if sep and candidate.isdigit():
            ports.add(int(candidate))
    return ports


class RealDockerCompose:
    def __init__(self, shell: ShellAdapter):
        self.shell = shell

    def _base(self, project: str, compose_dir: str) -> list[str]:
        if not _PROJECT_RE.match(project):
            raise ValueError(f"Nom de projet compose invalide : {project!r}")
        return [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            os.path.join(compose_dir, "docker-compose.yml"),
        ]

    async def _run_checked(self, action: str, command: list[str]) -> None:
        result = await self.shell.run(command)
        if result.returncode != 0:
            raise RuntimeError(
                f"{action} failed (rc={result.returncode}): {result.stderr or result.stdout}"
            )

    async def up(self, project: str, compose_dir: str) -> None:
        base = self._base(project, compose_dir)
        async with _COMPOSE_OP_LOCK:
            result = await self.shell.run([*base, "up", "-d"])
            if result.returncode == 0:
                return
            error = RuntimeError(
                f"docker compose up failed (rc={result.returncode}): "
                f"{result.stderr or result.stdout}"
            )
            if "already in use by container" not in (result.stderr + result.stdout):
                raise error
            # Conflit de nom : un `up` concurrent a probablement créé le
            # conteneur. On l'adopte s'il est bien le nôtre (labels projet +
            # service) et s'il tourne ; sinon l'erreur d'origine est relevée.
            if await self._adopt_running_container(project):
                logger.warning(
                    "Conteneur %s déjà créé par un `up` concurrent, adoption", project
                )
                return
            raise error

    async def down(self, project: str, compose_dir: str) -> None:
        async with _COMPOSE_OP_LOCK:
            await self._run_checked(
                "docker compose down",
                [*self._base(project, compose_dir), "down", "--remove-orphans"],
            )

    async def restart(self, project: str, compose_dir: str) -> None:
        async with _COMPOSE_OP_LOCK:
            await self._run_checked(
                "docker compose restart",
                [*self._base(project, compose_dir), "restart"],
            )

    async def remove(self, project: str, compose_dir: str) -> None:
        async with _COMPOSE_OP_LOCK:
            await self._run_checked(
                "docker compose remove",
                [*self._base(project, compose_dir), "down", "--remove-orphans", "-v"],
            )

    async def _adopt_running_container(self, project: str) -> bool:
        """Vrai si exactement un conteneur `running` porte les labels proxy du projet.

        Un conteneur non identifiable par ces deux labels n'est jamais adopté.
        """
        result = await self.shell.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                "label=com.docker.compose.service=proxy",
                "--format",
                "{{.Names}}|{{.State}}",
            ]
        )
        if result.returncode != 0:
            return False
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            return False
        return lines[0].rsplit("|", 1)[-1].strip() == "running"

    async def published_host_ports(self) -> set[int]:
        """Ports hôte publiés par tous les conteneurs (``docker ps -a``).

        Un seul appel borné ; docker indisponible (rc != 0) → ensemble vide.
        """
        result = await self.shell.run(
            ["docker", "ps", "-a", "--format", "{{.Ports}}"], timeout=5.0
        )
        if result.returncode != 0:
            return set()
        return parse_published_ports(result.stdout)

    async def state(self, project: str) -> str | None:
        if not _PROJECT_RE.match(project):
            raise ValueError(f"Nom de projet compose invalide : {project!r}")
        result = await self.shell.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.State}}",
            ]
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if line.strip():
                return line.strip()
        return None


class FakeDockerCompose:
    def __init__(self):
        self.projects: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.published_ports: set[int] = set()

    def set_state(self, project: str, state: str) -> None:
        self.projects[project] = state

    async def published_host_ports(self) -> set[int]:
        return set(self.published_ports)

    async def up(self, project: str, compose_dir: str) -> None:
        self.calls.append((project, "up"))
        self.projects[project] = "running"

    async def down(self, project: str, compose_dir: str) -> None:
        self.calls.append((project, "down"))
        if project in self.projects:
            self.projects[project] = "exited"

    async def restart(self, project: str, compose_dir: str) -> None:
        self.calls.append((project, "restart"))
        if project in self.projects:
            self.projects[project] = "running"

    async def remove(self, project: str, compose_dir: str) -> None:
        self.calls.append((project, "remove"))
        self.projects.pop(project, None)

    async def state(self, project: str) -> str | None:
        return self.projects.get(project)
