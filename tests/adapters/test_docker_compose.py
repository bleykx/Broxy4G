"""Tests de l'adaptateur docker compose (Fake + Real via FakeShell)."""

import asyncio

import pytest

import broxy4g.adapters.docker_compose as docker_compose_module
from broxy4g.adapters.docker_compose import (
    FakeDockerCompose,
    RealDockerCompose,
    parse_published_ports,
)
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
ADOPT_CMD = [
    "docker",
    "ps",
    "-a",
    "--filter",
    f"label=com.docker.compose.project={PROJECT}",
    "--filter",
    "label=com.docker.compose.service=proxy",
    "--format",
    "{{.Names}}|{{.State}}",
]
CONFLICT = (
    "Error response from daemon: Conflict. The container name "
    f"\"/broxy4g-proxy-{PROJECT}\" is already in use by container \"92711a9e\"."
)


def _ok_shell(command: list[str]) -> FakeShell:
    shell = FakeShell()
    shell.set_response(command, ShellResult(0, "", ""))
    return shell


@pytest.fixture(autouse=True)
def fresh_compose_op_lock():
    """Chaque test tourne dans sa propre boucle d'événements (pytest-asyncio) :
    le verrou de processus est réinitialisé pour qu'il ne reste pas lié à une
    boucle close (en production, une seule boucle existe pour toute l'application)."""
    docker_compose_module._COMPOSE_OP_LOCK = asyncio.Lock()
    yield


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


class _RecordingShell:
    """Faux shell qui mesure le nombre d'exécutions SIMULTANÉES de commandes."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls: list[list[str]] = []
        self.responses: dict[str, ShellResult] = {}

    def set_response(self, command: list[str], result: ShellResult) -> None:
        self.responses[" ".join(command)] = result

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        key = " ".join(command)
        self.calls.append(list(command))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)  # fenêtre de chevauchement détectable
        self.active -= 1
        return self.responses.get(key, ShellResult(1, "", "Command not found"))


class TestComposeOperationSerialization:
    """up/down/restart/remove sont single-flight à l'échelle du processus."""

    async def test_two_concurrent_ups_do_not_overlap(self):
        shell = _RecordingShell()
        shell.set_response(
            [*BASE, "up", "-d"], ShellResult(0, "", "")
        )
        compose = RealDockerCompose(shell)

        await asyncio.gather(
            compose.up(PROJECT, COMPOSE_DIR), compose.up(PROJECT, COMPOSE_DIR)
        )

        assert shell.max_active == 1  # aucun chevauchement
        assert shell.calls == [[*BASE, "up", "-d"]] * 2  # les deux réussissent

    async def test_up_and_down_from_distinct_instances_are_serialized(self):
        shell = _RecordingShell()
        shell.set_response([*BASE, "up", "-d"], ShellResult(0, "", ""))
        shell.set_response([*BASE, "down", "--remove-orphans"], ShellResult(0, "", ""))
        first, second = RealDockerCompose(shell), RealDockerCompose(shell)

        await asyncio.gather(
            first.up(PROJECT, COMPOSE_DIR), second.down(PROJECT, COMPOSE_DIR)
        )

        assert shell.max_active == 1


class TestUpAdoptionOnNameConflict:
    """Conflit "already in use" : adoption si le conteneur est bien le nôtre."""

    def _conflict_shell(self, adopt_stdout: str | None) -> FakeShell:
        shell = FakeShell()
        shell.set_response([*BASE, "up", "-d"], ShellResult(1, "Creating ...", CONFLICT))
        if adopt_stdout is not None:
            shell.set_response(ADOPT_CMD, ShellResult(0, adopt_stdout, ""))
        return shell

    async def test_conflict_with_unique_running_container_is_adopted(self):
        shell = self._conflict_shell("broxy4g-proxy-x|running\n")

        await RealDockerCompose(shell).up(PROJECT, COMPOSE_DIR)  # aucune exception

        assert [c[0] for c in shell.calls] == [[*BASE, "up", "-d"], ADOPT_CMD]

    @pytest.mark.parametrize(
        "adopt_stdout",
        ["broxy4g-proxy-x|exited\n", "", "a|running\nb|running\n", None],
        ids=["conteneur_exited", "aucun_conteneur", "plusieurs_conteneurs", "probe_en_erreur"],
    )
    async def test_conflict_without_running_container_raises(self, adopt_stdout):
        shell = self._conflict_shell(adopt_stdout)
        with pytest.raises(RuntimeError, match="already in use"):
            await RealDockerCompose(shell).up(PROJECT, COMPOSE_DIR)

    async def test_non_conflict_error_is_raised_unchanged(self):
        shell = FakeShell()
        shell.set_response([*BASE, "up", "-d"], ShellResult(1, "some other daemon error", ""))
        with pytest.raises(RuntimeError, match="some other daemon error"):
            await RealDockerCompose(shell).up(PROJECT, COMPOSE_DIR)
        # Pas de probe d'adoption pour une erreur sans signature de conflit.
        assert len(shell.calls) == 1


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

    async def test_published_ports_default_empty_and_settable(self):
        fake = FakeDockerCompose()
        assert await fake.published_host_ports() == set()
        fake.published_ports = {31201, 8099}
        assert await fake.published_host_ports() == {31201, 8099}


class TestPublishedHostPorts:
    """Parsing des ports publiés — formats réels de ``docker ps -a``."""

    PS_CMD = ["docker", "ps", "-a", "--format", "{{.Ports}}"]

    def test_parse_real_broxy4g_format(self):
        stdout = (
            "192.168.1.40:31205->3128/tcp\n"
            "192.168.1.40:31203->3128/tcp\n"
            "192.168.1.40:31202->3128/tcp\n"
            "192.168.1.40:31201->3128/tcp\n"
            "192.168.1.40:31204->3128/tcp\n"
        )
        assert parse_published_ports(stdout) == {31201, 31202, 31203, 31204, 31205}

    def test_parse_multiple_entries_same_line(self):
        # L'entrée sans « -> » (port interne non publié) est ignorée.
        stdout = "0.0.0.0:31201->3128/tcp, [::]:31201->3128/tcp, 8099/tcp\n"
        assert parse_published_ports(stdout) == {31201}

    def test_parse_ignores_unpublished_internal_ports(self):
        stdout = "3128/tcp\n"
        assert parse_published_ports(stdout) == set()

    def test_parse_binds_to_specific_ip(self):
        stdout = "127.0.0.1:31250->3128/tcp\n"
        assert parse_published_ports(stdout) == {31250}

    def test_parse_empty(self):
        assert parse_published_ports("") == set()
        assert parse_published_ports("\n\n") == set()

    async def test_adapter_returns_ports_on_success(self):
        shell = FakeShell()
        shell.set_response(
            self.PS_CMD, ShellResult(0, "192.168.1.40:31201->3128/tcp\n", "")
        )
        adapter = RealDockerCompose(shell)

        assert await adapter.published_host_ports() == {31201}
        assert shell.calls == [(self.PS_CMD, 5.0)]

    async def test_adapter_error_yields_empty_set(self):
        shell = FakeShell()  # par défaut : rc=1
        assert await RealDockerCompose(shell).published_host_ports() == set()
