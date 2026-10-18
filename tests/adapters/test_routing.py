import unittest
from unittest.mock import AsyncMock, patch

from broxy4g.adapters.routing import RoutingRoute, RoutingRule
from broxy4g.adapters.routing_real import RealRouting
from broxy4g.adapters.shell import ShellAdapter, ShellResult


def _routing_with_output(stdout: str, rule_output: str = "") -> RealRouting:
    from broxy4g.adapters.shell import FakeShell
    shell = FakeShell()
    shell.set_response(
        ["ip", "route", "show", "table", "10001"],
        ShellResult(returncode=0, stdout=stdout, stderr=""),
    )
    shell.set_response(
        ["ip", "rule", "show"],
        ShellResult(returncode=0, stdout=rule_output, stderr=""),
    )
    return RealRouting(shell)


class TestRealRouting(unittest.TestCase):
    def setUp(self):
        self.mock_shell = AsyncMock(spec=ShellAdapter)
        self.routing = RealRouting(self.mock_shell)

    @patch('asyncio.create_subprocess_exec')
    async def test_add_rule(self, mock_create_subprocess_exec):
        # Mock successful rule addition
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b'', b'')
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        await self.routing.add_rule(10001, 1)

        # Verify
        mock_create_subprocess_exec.assert_called_once_with(
            "ip", "rule", "add", "fwmark", "1", "table", "10001"
        )

    @patch('asyncio.create_subprocess_exec')
    async def test_remove_rule(self, mock_create_subprocess_exec):
        # Mock successful rule removal
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b'', b'')
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        await self.routing.remove_rule(10001, 1)

        # Verify
        mock_create_subprocess_exec.assert_called_once_with(
            "ip", "rule", "del", "fwmark", "1", "table", "10001"
        )

    @patch('asyncio.create_subprocess_exec')
    async def test_add_route(self, mock_create_subprocess_exec):
        # Mock successful route addition
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b'', b'')
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        await self.routing.add_route(10001, "default", None, "wwan0")

        # Verify
        mock_create_subprocess_exec.assert_called_once_with(
            "ip", "route", "add", "default", "dev", "wwan0", "table", "10001"
        )

    @patch('asyncio.create_subprocess_exec')
    async def test_remove_route(self, mock_create_subprocess_exec):
        # Mock successful route removal
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b'', b'')
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        await self.routing.remove_route(10001, "default")

        # Verify
        mock_create_subprocess_exec.assert_called_once_with(
            "ip", "route", "del", "default", "table", "10001"
        )


async def test_list_routes_parses_default_with_gateway_and_dashed_iface():
    """Échantillon réel : 'default via <gw> dev dongle-1336' + route non-default ignorée."""
    stdout = (
        "default via 10.159.56.49 dev dongle-1336 onlink\n"
        "10.159.56.32/27 dev dongle-1336 proto kernel scope link src 10.159.56.48\n"
    )
    routing = _routing_with_output(stdout)

    routes = await routing.list_routes(10001)

    assert routes == [RoutingRoute(table_id=10001, dest="default", via="10.159.56.49",
                                   dev="dongle-1336")]


async def test_list_routes_parses_default_without_gateway():
    """Échantillon réel : 'default dev wwan1 scope link' (gateway None)."""
    routing = _routing_with_output("default dev wwan1 scope link\n")

    routes = await routing.list_routes(10001)

    assert routes == [RoutingRoute(table_id=10001, dest="default", via=None, dev="wwan1")]


async def test_list_routes_parses_nm_foreign_default_route():
    """Ligne réelle de NetworkManager : ``proto dhcp`` + ``src`` doivent être
    rapportés pour que le writer reconnaisse la route étrangère."""
    stdout = (
        "default via 192.168.172.245 dev enx02360c7c147a "
        "proto dhcp src 192.168.172.79 metric 2023\n"
    )
    routing = _routing_with_output(stdout)

    routes = await routing.list_routes(10001)

    assert routes == [RoutingRoute(
        table_id=10001, dest="default", via="192.168.172.245",
        dev="enx02360c7c147a", proto="dhcp", src="192.168.172.79",
    )]


async def test_list_routes_our_default_route_keeps_proto_src_none():
    """Notre route (sans ``proto`` ni ``src`` dans la sortie ``ip``) :
    les deux attributs restent ``None``."""
    routing = _routing_with_output("default via 10.159.56.49 dev dongle-1336 onlink\n")

    routes = await routing.list_routes(10001)

    assert routes == [RoutingRoute(
        table_id=10001, dest="default", via="10.159.56.49",
        dev="dongle-1336", proto=None, src=None,
    )]


async def test_add_barrier_route_uses_exact_unreachable_command():
    """Plancher : la commande exacte est 'ip route replace unreachable default table <T>'."""
    shell = AsyncMock(spec=ShellAdapter)
    shell.run.return_value = ShellResult(returncode=0, stdout="", stderr="")
    routing = RealRouting(shell)

    await routing.add_barrier_route(10001)

    shell.run.assert_awaited_once_with(
        ["ip", "route", "replace", "unreachable", "default", "table", "10001"]
    )


async def test_list_routes_parses_unreachable_barrier():
    """'unreachable default' (plancher) doit être rapporté pour l'idempotence."""
    routing = _routing_with_output("unreachable default\n")

    routes = await routing.list_routes(10001)

    assert routes == [RoutingRoute(table_id=10001, dest="default", via=None, dev=None,
                                   type="unreachable")]


async def test_fake_barrier_route_floor_and_real_route_overrides_it():
    """FakeRouting : plancher sur table vide, écrasé par une route réelle."""
    from broxy4g.adapters.routing import FakeRouting
    fake = FakeRouting()

    assert await fake.list_routes(10001) == []

    await fake.add_barrier_route(10001)
    assert await fake.list_routes(10001) == [
        RoutingRoute(table_id=10001, dest="default", via=None, dev=None, type="unreachable")
    ]

    # Une route réelle écrase le plancher (une seule default par table).
    await fake.add_route(10001, "default", "10.159.56.49", "dongle-1336")
    assert await fake.list_routes(10001) == [
        RoutingRoute(table_id=10001, dest="default", via="10.159.56.49", dev="dongle-1336")
    ]


async def test_list_rules_parses_fwmark_and_source_rules():
    """Parser indépendant de l'ordre des champs (fwmark hexa + règle source)."""
    stdout = (
        "0:\tfrom all lookup local\n"
        "10001:\tfrom all fwmark 0x1 lookup 10001\n"
        "11001:\tfrom 10.159.56.48 lookup 10001\n"
        "32766:\tfrom all lookup main\n"
        "32767:\tfrom all lookup default\n"
    )
    routing = _routing_with_output("", rule_output=stdout)

    rules = await routing.list_rules()

    assert rules == [
        RoutingRule(table_id=10001, fwmark=1, priority=10001),
        RoutingRule(table_id=10001, fwmark=0, priority=11001, source="10.159.56.48"),
    ]