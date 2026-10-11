from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Le paramètre ``interface`` désigne l'ADRESSE IP SOURCE à épingler, pas le nom
# d'une interface réseau : ``curl --interface <IP>`` et ``ping -I <IP>`` acceptent
# une IP source et lient la connexion à cette source précise (l'uplink du dongle).


@runtime_checkable
class ConnectivityAdapter(Protocol):
    async def ping(
        self,
        host: str,
        count: int = 4,
        interface: str | None = None,
    ) -> "PingResult":
        """Ping ``host`` ; ``interface`` = IP source à épingler (``ping -I <IP>``)."""
        ...

    async def get_public_ip(
        self,
        interface: str | None = None,
    ) -> str | None:
        """IP publique vue via l'uplink ; ``interface`` = IP source (``curl --interface <IP>``)."""
        ...


@dataclass
class PingResult:
    host: str
    packets_sent: int
    packets_received: int
    min_ms: float | None
    avg_ms: float | None
    max_ms: float | None


class FakeConnectivity:
    def __init__(self):
        self._public_ip: str | None = None
        self._ping_results: dict[str, PingResult] = {}
        self.interface_calls: list[str | None] = []

    def set_public_ip(self, ip: str):
        self._public_ip = ip

    def set_ping_result(self, host: str, result: PingResult):
        self._ping_results[host] = result

    async def ping(
        self,
        host: str,
        count: int = 4,
        interface: str | None = None,
    ) -> PingResult:
        self.interface_calls.append(interface)
        return self._ping_results.get(
            host,
            PingResult(
                host=host,
                packets_sent=count,
                packets_received=count,
                min_ms=10.0,
                avg_ms=20.0,
                max_ms=30.0,
            ),
        )

    async def get_public_ip(
        self,
        interface: str | None = None,
    ) -> str | None:
        self.interface_calls.append(interface)
        return self._public_ip


# Real implementation
