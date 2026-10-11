from pydantic import BaseModel


class AllowedHostCreate(BaseModel):
    host: str


class AllowedHostDetail(BaseModel):
    host: str


class ProxyAutoCreateSetting(BaseModel):
    enabled: bool


class ProxyListenInterfaceSetting(BaseModel):
    interface_name: str | None = None
