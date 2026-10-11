import unittest
from unittest.mock import AsyncMock, patch

from broxy4g.adapters.docker_real import RealDocker
from broxy4g.adapters.shell import ShellAdapter


class TestRealDocker(unittest.TestCase):
    def setUp(self):
        self.mock_shell = AsyncMock(spec=ShellAdapter)
        self.docker = RealDocker(self.mock_shell)

    @patch('asyncio.create_subprocess_exec')
    async def test_create_container(self, mock_create_subprocess_exec):
        # Mock successful container creation
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b'', b'')
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        await self.docker.create_container(
            "test_container",
            "nginx:latest",
            {8080: 80},
            {"ENV1": "value1"}
        )

        # Verify
        mock_create_subprocess_exec.assert_called_once_with(
            "docker", "create", "--name", "test_container",
            "-p", "8080:80",
            "-e", "ENV1=value1",
            "nginx:latest"
        )

    @patch('asyncio.create_subprocess_exec')
    async def test_start_container(self, mock_create_subprocess_exec):
        # Mock successful container start
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b'', b'')
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        await self.docker.start_container("test_container")

        # Verify
        mock_create_subprocess_exec.assert_called_once_with(
            "docker", "start", "test_container"
        )

    @patch('asyncio.create_subprocess_exec')
    async def test_stop_container(self, mock_create_subprocess_exec):
        # Mock successful container stop
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b'', b'')
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        await self.docker.stop_container("test_container")

        # Verify
        mock_create_subprocess_exec.assert_called_once_with(
            "docker", "stop", "test_container"
        )

    @patch('asyncio.create_subprocess_exec')
    async def test_remove_container(self, mock_create_subprocess_exec):
        # Mock successful container removal
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b'', b'')
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        await self.docker.remove_container("test_container")

        # Verify
        mock_create_subprocess_exec.assert_called_once_with(
            "docker", "rm", "-f", "test_container"
        )

    @patch('asyncio.create_subprocess_exec')
    async def test_get_container_state(self, mock_create_subprocess_exec):
        # Mock successful container state retrieval
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b'running\n', b'')
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        result = await self.docker.get_container_state("test_container")

        # Verify
        self.assertEqual(result, 'running')
        mock_create_subprocess_exec.assert_called_once_with(
            "docker", "inspect", "--format='{{.State.Status}}'", "test_container"
        )

    @patch('asyncio.create_subprocess_exec')
    async def test_list_containers(self, mock_create_subprocess_exec):
        # Mock successful container listing
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (
            b'test_container1\ntest_container2\n',
            b''
        )
        mock_create_subprocess_exec.return_value = mock_process

        # Test
        result = await self.docker.list_containers("test")

        # Verify
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], 'test_container1')
        self.assertEqual(result[1], 'test_container2')