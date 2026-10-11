from .shell import ShellAdapter


class RealDocker:
    def __init__(self, shell: ShellAdapter):
        self.shell = shell

    async def _run_checked(self, action: str, command: list[str]) -> None:
        result = await self.shell.run(command)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker {action} failed (rc={result.returncode}): {result.stderr or result.stdout}"
            )

    async def create_container(self, name: str, image: str, ports: dict[int, int], env: dict[str, str]) -> None:
        cmd = ["docker", "create", "--name", name]
        for host_port, container_port in ports.items():
            cmd.extend(["-p", f"{host_port}:{container_port}"])
        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(image)
        await self._run_checked("create", cmd)

    async def start_container(self, name: str) -> None:
        await self._run_checked("start", ["docker", "start", name])

    async def stop_container(self, name: str) -> None:
        await self._run_checked("stop", ["docker", "stop", name])

    async def remove_container(self, name: str) -> None:
        await self._run_checked("rm", ["docker", "rm", "-f", name])

    async def get_container_state(self, name: str) -> str | None:
        result = await self.shell.run(["docker", "inspect", "--format='{{.State.Status}}'", name])
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    async def list_containers(self, prefix: str) -> list[str]:
        result = await self.shell.run(["docker", "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"])
        if result.returncode != 0:
            return []
        return [name.strip() for name in result.stdout.strip().split('\n') if name.strip()]

