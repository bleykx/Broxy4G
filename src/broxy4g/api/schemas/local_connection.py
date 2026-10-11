from uuid import UUID

from pydantic import BaseModel


class LocalConnectionDetail(BaseModel):
    interface_name: str
    ip: str | None
    kind: str
    enrolled: bool
    proxy_id: UUID | None


class LocalConnectionCreate(BaseModel):
    interface_name: str
    enabled: bool = True
