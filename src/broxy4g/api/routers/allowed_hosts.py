from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from broxy4g.api.deps import get_allowed_hosts_service, require_auth
from broxy4g.api.schemas.settings import AllowedHostCreate, AllowedHostDetail
from broxy4g.domain.allowed_hosts import normalize_allowed_host
from broxy4g.services.allowed_hosts import AllowedHostsService

router = APIRouter(dependencies=[Depends(require_auth)])


def _require_host(value: str) -> str:
    if not value.strip():
        raise HTTPException(status_code=422, detail="host requis")
    try:
        return normalize_allowed_host(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/allowed-hosts", response_model=list[AllowedHostDetail])
async def list_global(svc: AllowedHostsService = Depends(get_allowed_hosts_service)):
    return [AllowedHostDetail(host=h) for h in await svc.list_global()]


@router.post("/allowed-hosts", response_model=AllowedHostDetail, status_code=201)
async def add_global(
    body: AllowedHostCreate, svc: AllowedHostsService = Depends(get_allowed_hosts_service)
):
    return AllowedHostDetail(host=await svc.add_global(_require_host(body.host)))


@router.delete("/allowed-hosts", status_code=204)
async def remove_global(
    host: str, svc: AllowedHostsService = Depends(get_allowed_hosts_service)
):
    await svc.remove_global(_require_host(host))


@router.get("/proxies/{proxy_id}/allowed-hosts", response_model=list[AllowedHostDetail])
async def list_for_proxy(
    proxy_id: UUID, svc: AllowedHostsService = Depends(get_allowed_hosts_service)
):
    return [AllowedHostDetail(host=h) for h in await svc.list_for_proxy(proxy_id)]


@router.post("/proxies/{proxy_id}/allowed-hosts", response_model=AllowedHostDetail, status_code=201)
async def add_for_proxy(
    proxy_id: UUID,
    body: AllowedHostCreate,
    svc: AllowedHostsService = Depends(get_allowed_hosts_service),
):
    return AllowedHostDetail(host=await svc.add_for_proxy(proxy_id, _require_host(body.host)))


@router.delete("/proxies/{proxy_id}/allowed-hosts", status_code=204)
async def remove_for_proxy(
    proxy_id: UUID, host: str, svc: AllowedHostsService = Depends(get_allowed_hosts_service)
):
    await svc.remove_for_proxy(proxy_id, _require_host(host))
