"""Routes par défaut de la TABLE MAIN : RealRouting via FakeShell, sans argument table."""

from broxy4g.adapters.routing import RoutingRoute
from broxy4g.adapters.routing_real import RealRouting
from broxy4g.adapters.shell import FakeShell, ShellResult

DEFAULT_CMD = ["ip", "-o", "route", "show", "default"]


class RecordingShell(FakeShell):
    def __init__(self):
        super().__init__()
        self.calls: list[list[str]] = []

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        self.calls.append(list(command))
        return await super().run(command, timeout)


async def test_list_default_routes_parses_with_and_without_via():
    shell = FakeShell()
    shell.set_response(DEFAULT_CMD, ShellResult(0, (
        "default via 192.168.172.245 dev enx02360c7c147a proto dhcp metric 20101\n"
        "default dev eth0 proto static metric 100\n"
    ), ""))

    routes = await RealRouting(shell).list_default_routes()

    assert routes == [
        RoutingRoute(table_id=254, dest="default", via="192.168.172.245", dev="enx02360c7c147a"),
        RoutingRoute(table_id=254, dest="default", via=None, dev="eth0"),
    ]


async def test_list_default_routes_empty_on_error():
    shell = FakeShell()
    shell.set_response(DEFAULT_CMD, ShellResult(1, "", "SIOCGIFADDR: No such device"))

    assert await RealRouting(shell).list_default_routes() == []


async def test_list_default_routes_never_passes_table_argument():
    shell = RecordingShell()
    shell.set_response(DEFAULT_CMD, ShellResult(0, "default via 10.0.0.1 dev eth0\n", ""))

    await RealRouting(shell).list_default_routes()

    assert shell.calls == [DEFAULT_CMD]
    assert "table" not in DEFAULT_CMD


async def test_remove_default_route_uses_exact_command_without_table():
    shell = RecordingShell()
    shell.set_response(["ip", "route", "del", "default", "dev", "enx02360c7c147a"],
                       ShellResult(0, "", ""))
    routing = RealRouting(shell)

    assert await routing.remove_default_route("enx02360c7c147a") is True

    assert shell.calls == [["ip", "route", "del", "default", "dev", "enx02360c7c147a"]]
    assert "table" not in shell.calls[0]


async def test_remove_default_route_false_on_failure():
    shell = RecordingShell()
    shell.set_response(["ip", "route", "del", "default", "dev", "wwan1"],
                       ShellResult(2, "", "RTNETLINK answers: No such process"))

    assert await RealRouting(shell).remove_default_route("wwan1") is False
