from fastapi import APIRouter, Depends, HTTPException, Response, status

from broxy4g.api.deps import get_local_connections_service, require_auth
from broxy4g.api.routers.proxies import _detail
from broxy4g.api.schemas.local_connection import LocalConnectionCreate, LocalConnectionDetail
from broxy4g.api.schemas.proxy import ProxyWithPassword
from broxy4g.services.local_connections import LocalConnectionsService

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/local-connections", response_model=list[LocalConnectionDetail])
async def list_local_connections(
    svc: LocalConnectionsService = Depends(get_local_connections_service),
):
    connections = await svc.list_all()
    return [
        LocalConnectionDetail(
            interface_name=c.interface_name,
            ip=c.ip,
            kind=c.kind,
            enrolled=c.enrolled,
            proxy_id=c.proxy_id,
        )
        for c in connections
    ]


@router.post("/local-connections", response_model=ProxyWithPassword)
async def enroll_local_connection(
    body: LocalConnectionCreate,
    response: Response,
    svc: LocalConnectionsService = Depends(get_local_connections_service),
):
    if not body.interface_name:
        raise HTTPException(status_code=422, detail="interface_name requis")
    try:
        provisioned = await svc.enroll(body.interface_name, enabled=body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    response.status_code = status.HTTP_201_CREATED if provisioned.credentials else status.HTTP_200_OK
    password = provisioned.credentials.password if provisioned.credentials else None
    return ProxyWithPassword(proxy=_detail(provisioned.proxy), password=password)


@router.delete("/local-connections/{interface_name}", status_code=status.HTTP_204_NO_CONTENT)
async def unenroll_local_connection(
    interface_name: str,
    svc: LocalConnectionsService = Depends(get_local_connections_service),
):
    await svc.unenroll(interface_name)
