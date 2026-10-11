from typing import Protocol, runtime_checkable


@runtime_checkable
class DockerAdapter(Protocol):
    async def create_container(self, name: str, image: str, ports: dict[int, int], env: dict[str, str]) -> None:
        ...

    async def start_container(self, name: str) -> None:
        ...

    async def stop_container(self, name: str) -> None:
        ...

    async def remove_container(self, name: str) -> None:
        ...

    async def get_container_state(self, name: str) -> str | None:
        ...

    async def list_containers(self, prefix: str) -> list[str]:
        ...


class FakeDocker:
    def __init__(self):
        self.containers: dict[str, dict] = {}

    async def create_container(self, name: str, image: str, ports: dict[int, int], env: dict[str, str]) -> None:
        self.containers[name] = {
            "image": image,
            "ports": ports,
            "env": env,
            "state": "created"
        }

    async def start_container(self, name: str) -> None:
        if name in self.containers:
            self.containers[name]["state"] = "running"

    async def stop_container(self, name: str) -> None:
        if name in self.containers:
            self.containers[name]["state"] = "stopped"

    async def remove_container(self, name: str) -> None:
        if name in self.containers:
            del self.containers[name]

    async def get_container_state(self, name: str) -> str | None:
        container = self.containers.get(name)
        return container["state"] if container else None

    async def list_containers(self, prefix: str) -> list[str]:
        return [name for name in self.containers.keys() if name.startswith(prefix)]

    def set_crash(self, name: str):
        """Simule un crash du conteneur"""
        if name in self.containers:
            self.containers[name]["state"] = "crashed"


# Real implementation
