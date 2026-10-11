"""Adaptateur dédié à la CLI ``docker compose`` (un projet par proxy).

Distinct de :mod:`broxy4g.adapters.docker` (gestion de conteneurs unitaires).
"""

import os
import re
from typing import Protocol, runtime_checkable

from .shell import ShellAdapter

_PROJECT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


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
        await self._run_checked("docker compose up", [*self._base(project, compose_dir), "up", "-d"])

    async def down(self, project: str, compose_dir: str) -> None:
        await self._run_checked(
            "docker compose down", [*self._base(project, compose_dir), "down", "--remove-orphans"]
        )

    async def restart(self, project: str, compose_dir: str) -> None:
        await self._run_checked("docker compose restart", [*self._base(project, compose_dir), "restart"])

    async def remove(self, project: str, compose_dir: str) -> None:
        await self._run_checked(
            "docker compose remove",
            [*self._base(project, compose_dir), "down", "--remove-orphans", "-v"],
        )

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

    def set_state(self, project: str, state: str) -> None:
        self.projects[project] = state

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
