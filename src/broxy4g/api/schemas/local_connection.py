from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from .proxy import ProxyDetail


class LocalConnectionDetail(BaseModel):
    interface_name: str
    ip: str | None
    kind: str
    enrolled: bool
    proxy_id: UUID | None
    gateway: str | None = None
    link_state: Literal["up", "down", "unknown"] = "unknown"


class LocalConnectionView(BaseModel):
    interface_name: str
    ip: str | None
    kind: str
    gateway: str | None
    link_state: Literal["up", "down", "unknown"]
    dns: list[str] | None
    enrolled: bool
    proxy_id: UUID | None
    proxy: ProxyDetail | None
    proxy_health: Literal["healthy", "unhealthy", "unknown"] | None
    proxy_health_reason: str | None


class LocalConnectionCreate(BaseModel):
    interface_name: str
    enabled: bool = True
