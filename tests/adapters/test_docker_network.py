"""Tests pour l'adaptateur d'inspection des réseaux Docker (device bridge)."""

from broxy4g.adapters.docker_network import (
    _INSPECT_FORMAT,
    DockerNetworkInfo,
    FakeDockerNetwork,
    RealDockerNetwork,
)
from broxy4g.adapters.shell_real import FakeShell, ShellResult

COMMAND = ["docker", "network", "inspect", "broxy4g-net", "--format", _INSPECT_FORMAT]


def make_shell(stdout: str, returncode: int = 0) -> FakeShell:
    shell = FakeShell()
    shell.set_response(COMMAND, ShellResult(returncode, stdout, ""))
    return shell


async def test_inspect_parses_nominal_output():
    shell = make_shell("broxy4g-net|37221a02879eabcd1234|bridge|br-37221a02879e|172.28.0.0/16|172.28.0.1\n")
    adapter = RealDockerNetwork(shell)

    info = await adapter.inspect("broxy4g-net")

    assert info == DockerNetworkInfo(
        name="broxy4g-net",
        id="37221a02879eabcd1234",
        driver="bridge",
        bridge_device="br-37221a02879e",
        subnet="172.28.0.0/16",
        gateway="172.28.0.1",
    )
    assert shell.calls == [(COMMAND, 30.0)]


async def test_inspect_returns_none_on_nonzero_returncode():
    shell = make_shell("", returncode=1)  # réseau absent ou docker indisponible
    adapter = RealDockerNetwork(shell)

    assert await adapter.inspect("broxy4g-net") is None


async def test_inspect_derives_bridge_device_when_option_missing():
    # Option com.docker.network.bridge.name vide → convention br-<id[:12]>.
    shell = make_shell("broxy4g-net|37221a02879eabcd1234|bridge||172.28.0.0/16|172.28.0.1\n")
    adapter = RealDockerNetwork(shell)

    info = await adapter.inspect("broxy4g-net")

    assert info is not None
    assert info.bridge_device == "br-37221a02879e"


async def test_inspect_empty_subnet_and_gateway_become_none():
    shell = make_shell("broxy4g-net|37221a02879eabcd1234|bridge|br-x||\n")
    adapter = RealDockerNetwork(shell)

    info = await adapter.inspect("broxy4g-net")

    assert info is not None
    assert info.subnet is None
    assert info.gateway is None


async def test_fake_docker_network_tracks_calls_and_removal():
    fake = FakeDockerNetwork()
    info = DockerNetworkInfo(
        name="broxy4g-net",
        id="37221a02879eabcd1234",
        driver="bridge",
        bridge_device="br-37221a02879e",
        subnet="172.28.0.0/16",
        gateway="172.28.0.1",
    )

    assert await fake.inspect("broxy4g-net") is None
    fake.set_network(info)
    assert await fake.inspect("broxy4g-net") == info
    fake.remove("broxy4g-net")
    assert await fake.inspect("broxy4g-net") is None
    assert fake.calls == ["broxy4g-net", "broxy4g-net", "broxy4g-net"]
