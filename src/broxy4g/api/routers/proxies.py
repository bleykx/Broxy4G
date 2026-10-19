import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from broxy4g.api.deps import get_proxy_service, require_auth
from broxy4g.api.schemas.proxy import ProxyDetail, ProxyOptions, ProxyWithPassword
from broxy4g.domain.proxy import Proxy
from broxy4g.services.proxy_lifecycle import CredentialsUnavailable, ProxyLifecycleService

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])


def _detail(p: Proxy) -> ProxyDetail:
    return ProxyDetail(
        id=p.id,
        dongle_id=p.dongle_id,
        source_type=p.source_type.value,
        source_ref=p.source_ref,
        interface_name=p.interface_name,
        container_name=p.container_name,
        listen_port=p.listen_port,
        auth_user=p.auth_user,
        state=p.state.value,
        enabled=p.enabled,
        network_ip=p.network_ip,
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
    )


async def _load(svc: ProxyLifecycleService, proxy_id: UUID) -> Proxy:
    proxy = await svc.get(proxy_id)
    if proxy is None:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return proxy


@router.get("/proxies", response_model=list[ProxyDetail])
async def list_proxies(svc: ProxyLifecycleService = Depends(get_proxy_service)):
    return [_detail(p) for p in await svc.list_all()]


@router.get("/proxies/{proxy_id}", response_model=ProxyDetail)
async def get_proxy(proxy_id: UUID, svc: ProxyLifecycleService = Depends(get_proxy_service)):
    return _detail(await _load(svc, proxy_id))


@router.get("/dongles/{dongle_id}/proxy", response_model=ProxyDetail)
async def get_dongle_proxy(dongle_id: UUID, svc: ProxyLifecycleService = Depends(get_proxy_service)):
    proxy = await svc.get_by_dongle_id(dongle_id)
    if proxy is None:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return _detail(proxy)


@router.post("/dongles/{dongle_id}/proxy", response_model=ProxyWithPassword)
async def create_proxy(
    dongle_id: UUID,
    response: Response,
    body: ProxyOptions | None = None,
    svc: ProxyLifecycleService = Depends(get_proxy_service),
):
    enabled = body.enabled if body is not None else True
    try:
        provisioned = await svc.create(dongle_id, enabled=enabled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    response.status_code = status.HTTP_201_CREATED if provisioned.credentials else status.HTTP_200_OK
    password = provisioned.credentials.password if provisioned.credentials else None
    return ProxyWithPassword(proxy=_detail(provisioned.proxy), password=password)


@router.post("/proxies/{proxy_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_proxy(proxy_id: UUID, svc: ProxyLifecycleService = Depends(get_proxy_service)):
    await _load(svc, proxy_id)
    await svc.start(proxy_id)


@router.post("/proxies/{proxy_id}/stop", status_code=status.HTTP_202_ACCEPTED)
async def stop_proxy(proxy_id: UUID, svc: ProxyLifecycleService = Depends(get_proxy_service)):
    await _load(svc, proxy_id)
    await svc.stop(proxy_id)


@router.post("/proxies/{proxy_id}/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart_proxy(proxy_id: UUID, svc: ProxyLifecycleService = Depends(get_proxy_service)):
    await _load(svc, proxy_id)
    await svc.restart(proxy_id)


@router.post("/proxies/{proxy_id}/rotate", response_model=ProxyWithPassword)
async def rotate_proxy(proxy_id: UUID, svc: ProxyLifecycleService = Depends(get_proxy_service)):
    await _load(svc, proxy_id)
    credentials = await svc.rotate_credentials(proxy_id)
    proxy = await _load(svc, proxy_id)
    return ProxyWithPassword(proxy=_detail(proxy), password=credentials.password)


@router.get("/proxies/{proxy_id}/credentials", response_model=ProxyWithPassword)
async def get_proxy_credentials(
    proxy_id: UUID,
    response: Response,
    svc: ProxyLifecycleService = Depends(get_proxy_service),
):
    proxy = await _load(svc, proxy_id)  # 404 si proxy inconnu
    try:
        credentials = await svc.get_credentials(proxy_id)
    except CredentialsUnavailable as exc:
        # Le proxy existe mais son secret n'est pas récupérable : 409 (conflit
        # entre la demande et l'état réel) plutôt que 404 (réservé à l'inconnu).
        logger.warning("Consulte identifiants proxy %s : non récupérables (%s)", proxy_id, exc)
        raise HTTPException(status_code=409, detail=str(exc))
    logger.warning("ACTION ADMIN : consultation des identifiants du proxy %s", proxy_id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return ProxyWithPassword(proxy=_detail(proxy), password=credentials.password)


@router.delete("/proxies/{proxy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_proxy(proxy_id: UUID, svc: ProxyLifecycleService = Depends(get_proxy_service)):
    await _load(svc, proxy_id)
    await svc.remove(proxy_id)
