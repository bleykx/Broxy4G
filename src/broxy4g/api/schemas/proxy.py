from uuid import UUID

from pydantic import BaseModel


class ProxyOptions(BaseModel):
    enabled: bool = True


class ProxyDetail(BaseModel):
    id: UUID
    dongle_id: UUID | None
    source_type: str
    source_ref: str | None
    interface_name: str | None
    container_name: str
    listen_port: int
    auth_user: str | None
    state: str
    enabled: bool
    network_ip: str | None
    created_at: str
    updated_at: str


class ProxyWithPassword(BaseModel):
    proxy: ProxyDetail
    password: str | None = None
