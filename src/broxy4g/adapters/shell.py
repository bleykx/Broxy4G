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

    def set_response(self, command: list[str], result: ShellResult):
        self.responses[" ".join(command)] = result

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        cmd_key = " ".join(command)
        if cmd_key in self.responses:
            return self.responses[cmd_key]
        return ShellResult(returncode=1, stdout="", stderr="Command not found")


# Real implementation
# Already imported at the top: from .shell_real import RealShell