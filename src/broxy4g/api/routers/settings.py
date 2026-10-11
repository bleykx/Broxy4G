from fastapi import APIRouter, Depends, HTTPException

from broxy4g.api.deps import get_proxy_listen_service, get_settings_repo, require_auth
from broxy4g.api.schemas.settings import ProxyAutoCreateSetting, ProxyListenInterfaceSetting
from broxy4g.domain.proxy_listen import InvalidListenInterface
from broxy4g.repositories.settings_repository import AUTO_CREATE_PROXY_KEY, SettingsRepository
from broxy4g.services.proxy_listen import ProxyListenService

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/settings/proxy-auto-create", response_model=ProxyAutoCreateSetting)
async def get_proxy_auto_create(repo: SettingsRepository = Depends(get_settings_repo)):
    return ProxyAutoCreateSetting(enabled=await repo.get_bool(AUTO_CREATE_PROXY_KEY, True))


@router.put("/settings/proxy-auto-create", response_model=ProxyAutoCreateSetting)
async def set_proxy_auto_create(
    body: ProxyAutoCreateSetting, repo: SettingsRepository = Depends(get_settings_repo)
):
    await repo.set_bool(AUTO_CREATE_PROXY_KEY, body.enabled)
    return body


@router.get("/settings/proxy-listen-interface", response_model=ProxyListenInterfaceSetting)
async def get_proxy_listen_interface(service: ProxyListenService = Depends(get_proxy_listen_service)):
    return ProxyListenInterfaceSetting(interface_name=await service.get_interface())


@router.put("/settings/proxy-listen-interface", response_model=ProxyListenInterfaceSetting)
async def set_proxy_listen_interface(
    body: ProxyListenInterfaceSetting, service: ProxyListenService = Depends(get_proxy_listen_service)
):
    try:
        name = await service.set_interface(body.interface_name)
    except InvalidListenInterface as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ProxyListenInterfaceSetting(interface_name=name)
