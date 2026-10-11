import unittest
from unittest.mock import AsyncMock, patch

from broxy4g.adapters.shell_real import RealShell


class TestRealShell(unittest.TestCase):
    def setUp(self):
        self.shell = RealShell()

    @patch('asyncio.create_subprocess_exec')
    async def test_run_success(self, mock_create_subprocess_exec):
        # Setup mock
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b"stdout content", b"stderr content")
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        result = await self.shell.run(["ls", "-l"])

        # Verify
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "stdout content")
        self.assertEqual(result.stderr, "stderr content")

    @patch('asyncio.create_subprocess_exec')
    async def test_run_timeout(self, mock_create_subprocess_exec):
        # Setup mock to raise timeout
        mock_create_subprocess_exec.side_effect = TimeoutError()

        # Test
        result = await self.shell.run(["ls", "-l"])

        # Verify
        self.assertEqual(result.returncode, -1)
        self.assertEqual(result.stderr, "timeout")

    @patch('asyncio.create_subprocess_exec')
    async def test_run_error(self, mock_create_subprocess_exec):
        # Setup mock
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate.return_value = (b"", b"error content")
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        result = await self.shell.run(["ls", "-l"])

        # Verify
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "error content")