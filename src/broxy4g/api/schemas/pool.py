from uuid import UUID

from pydantic import BaseModel


class PoolCreate(BaseModel):
    label: str


class PoolUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None


class PoolMemberAdd(BaseModel):
    proxy_id: UUID


class PoolMemberStatus(BaseModel):
    proxy_id: UUID
    eligible: bool
    reason: str | None
    target: str | None  # "host:port" du membre si éligible, sinon None


class PoolDetail(BaseModel):
    id: UUID
    label: str
    listen_port: int
    auth_user: str | None
    enabled: bool
    created_at: str
    updated_at: str
    listening: bool
    bind_ip: str | None
    listen_error: str | None
    published_ip: str | None
    endpoint: str | None  # "ip:port" à communiquer au client
    members: list[PoolMemberStatus]
    eligible_count: int


class PoolWithPassword(BaseModel):
    pool: PoolDetail
    password: str | None = None
