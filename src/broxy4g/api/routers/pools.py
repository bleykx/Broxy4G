"""Routes API des pools de proxies (authentification obligatoire).

Sérialisation seulement : toute la logique métier vit dans ``PoolService``.
Le mot de passe clair n'est jamais journalisé (seul le ``pool_id`` l'est).
"""

import logging
from contextlib import contextmanager
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from broxy4g.api.deps import get_pool_service, require_auth
from broxy4g.api.schemas.pool import (
    PoolCreate,
    PoolDetail,
    PoolMemberAdd,
    PoolMemberStatus,
    PoolUpdate,
    PoolWithPassword,
)
from broxy4g.services.pool_ports import PortRangeExhausted
from broxy4g.services.pool_service import (
    PoolCredentialsUnavailable,
    PoolMemberInvalid,
    PoolNotFound,
    PoolService,
    PoolStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)], tags=["pools"])


def _detail(status: PoolStatus) -> PoolDetail:
    """Vue API d'un statut agrégé : jamais d'identifiants de membres."""
    members = [
        PoolMemberStatus(
            proxy_id=m.proxy_id,
            eligible=m.eligible,
            reason=m.reason,
            target=f"{m.candidate.host}:{m.candidate.port}" if m.eligible and m.candidate else None,
        )
        for m in status.members
    ]
    return PoolDetail(
        id=status.pool.id,
        label=status.pool.label,
        listen_port=status.pool.listen_port,
        auth_user=status.pool.auth_user,
        enabled=status.pool.enabled,
        created_at=status.pool.created_at.isoformat(),
        updated_at=status.pool.updated_at.isoformat(),
        listening=status.listen.listening,
        bind_ip=status.listen.bind_ip,
        listen_error=status.listen.error,
        published_ip=status.published_ip,
        endpoint=status.endpoint,
        members=members,
        eligible_count=sum(1 for m in members if m.eligible),
    )


@contextmanager
def _errors():
    """Mappe les exceptions métier du service sur des codes HTTP stables."""
    try:
        yield
    except PoolNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except PoolMemberInvalid as exc:
        raise HTTPException(404, str(exc)) from exc
    except PoolCredentialsUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except PortRangeExhausted as exc:
        raise HTTPException(409, "plage de ports des pools épuisée") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/pools", response_model=list[PoolDetail])
async def list_pools(svc: PoolService = Depends(get_pool_service)):
    """Liste tous les pools avec leur écoute et leurs membres évalués."""
    return [_detail(s) for s in await svc.list_statuses()]


@router.post("/pools", response_model=PoolWithPassword, status_code=status.HTTP_201_CREATED)
async def create_pool(body: PoolCreate, svc: PoolService = Depends(get_pool_service)):
    """Crée un pool ; le mot de passe clair est renvoyé une seule fois."""
    with _errors():
        pool, password = await svc.create(body.label)
        logger.warning("ACTION ADMIN : création du pool %s (port %s)", pool.id, pool.listen_port)
        detail = _detail(await svc.status(pool.id))
    return PoolWithPassword(pool=detail, password=password)


@router.get("/pools/{pool_id}", response_model=PoolDetail)
async def get_pool(pool_id: UUID, svc: PoolService = Depends(get_pool_service)):
    """Détail d'un pool : écoute, membres éligibles/exclus, endpoint."""
    with _errors():
        return _detail(await svc.status(pool_id))


@router.patch("/pools/{pool_id}", response_model=PoolDetail)
async def update_pool(pool_id: UUID, body: PoolUpdate, svc: PoolService = Depends(get_pool_service)):
    """Met à jour ``label`` et/ou ``enabled`` du pool."""
    with _errors():
        await svc.update(pool_id, label=body.label, enabled=body.enabled)
        if body.enabled is not None:
            action = "activation" if body.enabled else "désactivation"
            logger.warning("ACTION ADMIN : %s du pool %s", action, pool_id)
        return _detail(await svc.status(pool_id))


@router.delete("/pools/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pool(pool_id: UUID, svc: PoolService = Depends(get_pool_service)):
    """Supprime le pool et ses membres."""
    with _errors():
        await svc.delete(pool_id)
        logger.warning("ACTION ADMIN : suppression du pool %s", pool_id)


@router.post("/pools/{pool_id}/members", response_model=PoolDetail)
async def add_pool_member(
    pool_id: UUID,
    response: Response,
    body: PoolMemberAdd,
    svc: PoolService = Depends(get_pool_service),
):
    """Ajoute un proxy au pool (idempotent : 201 si ajouté, 200 sinon)."""
    with _errors():
        added = await svc.add_member(pool_id, body.proxy_id)
        logger.warning("ACTION ADMIN : ajout du proxy %s au pool %s", body.proxy_id, pool_id)
        response.status_code = status.HTTP_201_CREATED if added else status.HTTP_200_OK
        return _detail(await svc.status(pool_id))


@router.delete("/pools/{pool_id}/members/{proxy_id}", response_model=PoolDetail)
async def remove_pool_member(
    pool_id: UUID,
    proxy_id: UUID,
    svc: PoolService = Depends(get_pool_service),
):
    """Retire un proxy du pool."""
    with _errors():
        await svc.remove_member(pool_id, proxy_id)
        logger.warning("ACTION ADMIN : retrait du proxy %s du pool %s", proxy_id, pool_id)
        return _detail(await svc.status(pool_id))


@router.get("/pools/{pool_id}/status", response_model=PoolDetail)
async def pool_status(pool_id: UUID, svc: PoolService = Depends(get_pool_service)):
    """Alias contractuel de ``GET /pools/{pool_id}`` (même agrégat)."""
    with _errors():
        return _detail(await svc.status(pool_id))


@router.get("/pools/{pool_id}/credentials", response_model=PoolWithPassword)
async def get_pool_credentials(
    pool_id: UUID,
    response: Response,
    svc: PoolService = Depends(get_pool_service),
):
    """Identifiants du pool (no-store, accès journalisé ; jamais le mot de passe)."""
    with _errors():
        await svc.get(pool_id)  # 404 si pool inconnu
        try:
            _, password = await svc.credentials(pool_id)
        except PoolCredentialsUnavailable as exc:
            logger.warning("Identifiants du pool %s non récupérables (%s)", pool_id, exc)
            raise HTTPException(409, str(exc)) from exc
        logger.warning("ACTION ADMIN : consultation des identifiants du pool %s", pool_id)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        detail = _detail(await svc.status(pool_id))
    return PoolWithPassword(pool=detail, password=password)
