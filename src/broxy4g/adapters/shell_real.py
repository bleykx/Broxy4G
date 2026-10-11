import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class ShellAdapter(Protocol):
    async def run(self, command: list[str], timeout: float = 30.0) -> "ShellResult":
        ...


@dataclass
class ShellResult:
    returncode: int
    stdout: str
    stderr: str


class FakeShell:
    def __init__(self):
        self.responses: dict[str, ShellResult] = {}
        self.calls: list[tuple[list[str], float]] = []

    def set_response(self, command: list[str], result: ShellResult):
        self.responses[" ".join(command)] = result

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        cmd_key = " ".join(command)
        self.calls.append((command, timeout))
        if cmd_key in self.responses:
            return self.responses[cmd_key]
        return ShellResult(returncode=1, stdout="", stderr="Command not found")


class RealShell:
    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            return ShellResult(
                returncode=process.returncode or 0,
                stdout=stdout.decode(),
                stderr=stderr.decode()
            )
        except TimeoutError:
            return ShellResult(
                returncode=-1,
                stdout="",
                stderr=f"command timed out after {timeout}s: {' '.join(command)}",
            )