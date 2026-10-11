"""Tests de l'adaptateur docker compose (Fake + Real via FakeShell)."""

import pytest

from broxy4g.adapters.docker_compose import FakeDockerCompose, RealDockerCompose
from broxy4g.adapters.shell_real import FakeShell, ShellResult

PROJECT = "p1"
COMPOSE_DIR = "/opt/proxies/p1"
BASE = ["docker", "compose", "-p", PROJECT, "-f", "/opt/proxies/p1/docker-compose.yml"]
STATE_CMD = [
    "docker",
    "ps",
    "-a",
    "--filter",
    f"label=com.docker.compose.project={PROJECT}",
    "--format",
    "{{.State}}",
]


def _ok_shell(command: list[str]) -> FakeShell:
    shell = FakeShell()
    shell.set_response(command, ShellResult(0, "", ""))
    return shell


class TestRealDockerComposeCommands:
    async def test_up_command(self):
        shell = _ok_shell([*BASE, "up", "-d"])
        await RealDockerCompose(shell).up(PROJECT, COMPOSE_DIR)
        assert shell.calls[-1][0] == [*BASE, "up", "-d"]

    async def test_down_command(self):
        shell = _ok_shell([*BASE, "down", "--remove-orphans"])
        await RealDockerCompose(shell).down(PROJECT, COMPOSE_DIR)
        assert shell.calls[-1][0] == [*BASE, "down", "--remove-orphans"]

    async def test_restart_command(self):
        shell = _ok_shell([*BASE, "restart"])
        await RealDockerCompose(shell).restart(PROJECT, COMPOSE_DIR)
        assert shell.calls[-1][0] == [*BASE, "restart"]

    async def test_remove_command(self):
        shell = _ok_shell([*BASE, "down", "--remove-orphans", "-v"])
        await RealDockerCompose(shell).remove(PROJECT, COMPOSE_DIR)
        assert shell.calls[-1][0] == [*BASE, "down", "--remove-orphans", "-v"]

    @pytest.mark.parametrize("action", ["up", "down", "restart", "remove"])
    async def test_raises_on_failure(self, action):
        shell = FakeShell()  # par défaut : rc=1
        with pytest.raises(RuntimeError):
            await getattr(RealDockerCompose(shell), action)(PROJECT, COMPOSE_DIR)


class TestRealDockerComposeState:
    async def test_returns_first_nonempty_line(self):
        shell = FakeShell()
        shell.set_response(STATE_CMD, ShellResult(0, "\nrunning\nexited\n", ""))
        assert await RealDockerCompose(shell).state(PROJECT) == "running"

    async def test_single_container(self):
        shell = FakeShell()
        shell.set_response(STATE_CMD, ShellResult(0, "running\n", ""))
        assert await RealDockerCompose(shell).state(PROJECT) == "running"

    async def test_none_on_error(self):
        shell = FakeShell()  # par défaut : rc=1
        assert await RealDockerCompose(shell).state(PROJECT) is None

    async def test_none_on_empty_output(self):
        shell = FakeShell()
        shell.set_response(STATE_CMD, ShellResult(0, "", ""))
        assert await RealDockerCompose(shell).state(PROJECT) is None


class TestProjectValidation:
    @pytest.mark.parametrize("project", ["-p", "a b", "x;y", "", "_lead", "p1|rm"])
    async def test_invalid_project_raises_on_up(self, project):
        with pytest.raises(ValueError):
            await RealDockerCompose(FakeShell()).up(project, COMPOSE_DIR)

    @pytest.mark.parametrize("project", ["-p", "a b", "x;y", ""])
    async def test_invalid_project_raises_on_state(self, project):
        with pytest.raises(ValueError):
            await RealDockerCompose(FakeShell()).state(project)

    async def test_valid_project_names_accepted(self):
        for project in ("p1", "a.b-c_d", "A1"):
            RealDockerCompose(FakeShell())._base(project, COMPOSE_DIR)


class TestFakeDockerCompose:
    async def test_lifecycle(self):
        fake = FakeDockerCompose()
        assert await fake.state(PROJECT) is None

        await fake.up(PROJECT, COMPOSE_DIR)
        assert await fake.state(PROJECT) == "running"
        assert fake.calls[-1] == (PROJECT, "up")

        await fake.restart(PROJECT, COMPOSE_DIR)
        assert await fake.state(PROJECT) == "running"

        await fake.down(PROJECT, COMPOSE_DIR)
        assert await fake.state(PROJECT) == "exited"

        await fake.remove(PROJECT, COMPOSE_DIR)
        assert await fake.state(PROJECT) is None
        assert fake.calls == [(PROJECT, "up"), (PROJECT, "restart"), (PROJECT, "down"), (PROJECT, "remove")]

    async def test_set_state(self):
        fake = FakeDockerCompose()
        fake.set_state(PROJECT, "exited")
        assert await fake.state(PROJECT) == "exited"

    async def test_down_unknown_project_is_noop(self):
        fake = FakeDockerCompose()
        await fake.down(PROJECT, COMPOSE_DIR)
        assert await fake.state(PROJECT) is None
