"""Tests RealAdb : FakeShell avec réponses ``ShellResult`` programmées."""

import logging

import pytest

from broxy4g.adapters.adb import AdbDevice, FakeAdb
from broxy4g.adapters.adb_real import RealAdb
from broxy4g.adapters.shell import FakeShell, ShellResult

ADB = "adb"
SERIAL = "R58N729DZMV"

DEVICES_OUTPUT = """\
List of devices attached
R58N729DZMV        device usb:1-2 product:SM-A217F model:SM_A217F device:a21 transport_id:1
ABCD1234           offline usb:1-3
* daemon started successfully
"""


def result(returncode: int, stdout: str = "") -> ShellResult:
    return ShellResult(returncode=returncode, stdout=stdout, stderr="")


class RecordingFakeShell(FakeShell):
    """FakeShell + enregistrement des commandes + réponses séquentielles par commande."""

    def __init__(self):
        super().__init__()
        self.sequences: dict[str, list[ShellResult]] = {}
        self.commands: list[list[str]] = []

    def set_sequence(self, command: list[str], results: list[ShellResult]) -> None:
        self.sequences[" ".join(command)] = list(results)

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        self.commands.append(list(command))
        pending = self.sequences.get(" ".join(command))
        if pending:
            return pending.pop(0)
        return await super().run(command, timeout)


@pytest.mark.asyncio
async def test_list_devices_parses_real_adb_devices_l_output():
    shell = RecordingFakeShell()
    shell.set_response([ADB, "devices", "-l"], result(0, DEVICES_OUTPUT))
    devices = await RealAdb(shell).list_devices()
    assert devices[0] == AdbDevice(
        serial="R58N729DZMV", state="device", model="SM_A217F", product="SM-A217F"
    )
    assert devices[1] == AdbDevice(serial="ABCD1234", state="offline")
    assert len(devices) == 2  # ligne daemon et en-tête ignorés


@pytest.mark.asyncio
async def test_list_devices_rc_nonzero_warns_once_per_instance(caplog):
    """rc != 0 → liste vide + un seul WARNING par instance (pas de spam toutes les ~10 s)."""
    shell = RecordingFakeShell()
    shell.set_response(
        [ADB, "devices", "-l"], ShellResult(returncode=127, stdout="", stderr="Command not found: adb")
    )
    adb = RealAdb(shell)
    with caplog.at_level(logging.WARNING):
        assert await adb.list_devices() == []
        assert await adb.list_devices() == []
    failures = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(failures) == 1
    message = failures[0].getMessage()
    assert ADB in message  # chemin du binaire utilisé
    assert "127" in message  # code retour
    assert "Command not found" in message  # extrait de stderr
    assert "absent ou non autorisé" in message


class RaisingFakeShell(FakeShell):
    """Shell dont ``run`` lève ``OSError`` (binaire présent mais non autorisé)."""

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        raise PermissionError(13, "Operation not permitted", command[0])


@pytest.mark.asyncio
async def test_list_devices_oserror_returns_empty_and_warns_once(caplog):
    """OSError du shell → liste vide (aucune exception) + un seul WARNING par instance."""
    adb = RealAdb(RaisingFakeShell())
    with caplog.at_level(logging.WARNING):
        assert await adb.list_devices() == []
        assert await adb.list_devices() == []
    failures = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(failures) == 1
    assert "PermissionError" in failures[0].getMessage()


@pytest.mark.asyncio
async def test_enable_tethering_single_function_and_retry_when_not_applied():
    shell = RecordingFakeShell()
    set_cmd = [ADB, "-s", SERIAL, "shell", "svc", "usb", "setFunctions", "rndis"]
    prop_cmd = [ADB, "-s", SERIAL, "shell", "getprop", "sys.usb.state"]
    shell.set_response(set_cmd, result(0))
    shell.set_sequence(prop_cmd, [result(0, "mtp,adb"), result(0, "rndis,adb")])

    assert await RealAdb(shell).enable_usb_tethering(SERIAL, attempts=3, retry_delay=0.01) is True

    # Jamais de liste de fonctions séparée par virgule.
    assert all("," not in part for command in shell.commands for part in command)
    # Le 1er essai n'a pas changé sys.usb.state → vérifié et réessayé.
    assert shell.commands.count(set_cmd) == 2
    assert shell.commands.count(prop_cmd) == 2


@pytest.mark.asyncio
async def test_enable_tethering_rc255_is_transient_never_success():
    shell = RecordingFakeShell()
    set_cmd = [ADB, "-s", SERIAL, "shell", "svc", "usb", "setFunctions", "rndis"]
    prop_cmd = [ADB, "-s", SERIAL, "shell", "getprop", "sys.usb.state"]
    shell.set_response(set_cmd, result(255))
    shell.set_response(prop_cmd, result(255))

    assert await RealAdb(shell).enable_usb_tethering(SERIAL, attempts=2, retry_delay=0.01) is False


@pytest.mark.asyncio
async def test_disable_tethering_restores_mtp():
    shell = RecordingFakeShell()
    shell.set_response(
        [ADB, "-s", SERIAL, "shell", "svc", "usb", "setFunctions", "mtp"], result(0)
    )
    shell.set_response([ADB, "-s", SERIAL, "shell", "getprop", "sys.usb.state"], result(0, "mtp,adb"))
    assert await RealAdb(shell).disable_usb_tethering(SERIAL) is True


@pytest.mark.asyncio
async def test_airplane_cycle_issues_four_commands_in_order():
    shell = RecordingFakeShell()
    expected = [
        [ADB, "-s", SERIAL, "shell", "cmd", "connectivity", "airplane-mode", mode]
        for mode in ("enable", "disable", "enable", "disable")
    ]
    for command in expected:
        shell.set_response(command, result(0))

    assert await RealAdb(shell).airplane_cycle(SERIAL, delay=0.01) is True
    assert shell.commands == expected


@pytest.mark.asyncio
async def test_wait_for_device_returns_true_when_state_device():
    shell = RecordingFakeShell()
    shell.set_response(
        [ADB, "devices", "-l"],
        result(0, "List of devices attached\nR58N729DZMV        device usb:1-2\n"),
    )
    assert await RealAdb(shell).wait_for_device(SERIAL, timeout=1.0, poll=0.01) is True


@pytest.mark.asyncio
async def test_wait_for_device_returns_false_on_timeout():
    shell = RecordingFakeShell()
    shell.set_response(
        [ADB, "devices", "-l"],
        result(0, "List of devices attached\nR58N729DZMV        offline usb:1-2\n"),
    )
    assert await RealAdb(shell).wait_for_device(SERIAL, timeout=0.05, poll=0.02) is False


@pytest.mark.asyncio
async def test_restart_server_issues_kill_start_reconnect_and_swallows_errors():
    """Relance bornée : kill (échec toléré) puis start + reconnect offline, en shell simulé."""
    shell = RecordingFakeShell()
    shell.set_response([ADB, "kill-server"], result(0))
    shell.set_response([ADB, "start-server"], result(0))
    shell.set_response([ADB, "reconnect", "offline"], result(0))

    assert await RealAdb(shell).restart_server() is True

    assert [ADB, "kill-server"] in shell.commands
    assert [ADB, "start-server"] in shell.commands
    assert [ADB, "reconnect", "offline"] in shell.commands


@pytest.mark.asyncio
async def test_restart_server_false_when_start_fails_and_no_reconnect():
    """Si ``start-server`` échoue, retour False et aucun ``reconnect`` (aucune exception)."""
    shell = RecordingFakeShell()
    shell.set_response([ADB, "kill-server"], result(1))  # échec toléré (pas de serveur)
    shell.set_response([ADB, "start-server"], result(1))

    assert await RealAdb(shell).restart_server() is False
    assert [ADB, "reconnect", "offline"] not in shell.commands


@pytest.mark.asyncio
async def test_fake_adb_restart_knob_and_counter():
    fake = FakeAdb()
    assert await fake.restart_server() is True
    assert fake.restart_count == 1
    fake.fail_restart_times = 1
    assert await fake.restart_server() is False
    assert fake.restart_count == 2


@pytest.mark.asyncio
async def test_fake_adb_enable_retries_with_fail_knob():
    fake = FakeAdb()
    fake.fail_enable_times = 1
    assert await fake.enable_usb_tethering(SERIAL, attempts=3, retry_delay=0.01) is True
    assert fake.usb_state == "rndis,adb"
    assert fake.calls.count(["svc", "usb", "setFunctions", "rndis"]) == 2
    assert all("," not in part for call in fake.calls for part in call)
