import unittest
from unittest.mock import AsyncMock, patch

from broxy4g.adapters.routing_real import RealRouting
from broxy4g.adapters.shell import ShellAdapter


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