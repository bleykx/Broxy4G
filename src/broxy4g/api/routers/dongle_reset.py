"""Routes de reset de dongle (batch POST + single POST/GET + déverrouillage).

Le single reset est aussi exposé en GET pour les outils externes GET-only
(Ankabot) qui ne peuvent passer qu'une URL. La logique métier reste celle du
``ResetService`` (idempotence, verrou par dongle, statuts
``DONE``/``ERROR``/``PENDING``/``LOCK``).

Le paramètre de query optionnel ``?lock=<secondes>`` arme, à la fin d'un reset
réellement ``DONE``, un verrou manuel temporaire par dongle qui bloque
uniquement les resets suivants (statut ``LOCK``) : le proxy continue de servir.
``GET /dongles/{id}/unlock`` lève ce verrou (idempotent, authentifié).

La variante GET renvoie le statut métier brut en ``text/plain`` (corps exact,
sans newline, HTTP 200) : Ankabot compare le corps de la réponse à la valeur
``DONE``.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from broxy4g.api.deps import get_reset_service, require_auth
from broxy4g.api.schemas.reset import (
    ResetBatchItem,
    ResetBatchResult,
    ResetRequest,
    ResetResult,
)
from broxy4g.domain.reset_lockout import MAX_LOCK_SECONDS
from broxy4g.services.reset import ResetService

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])


def _parse_lock_seconds(request: Request) -> float | None:
    """Lit le paramètre optionnel ``lock`` (secondes, 0 à ``MAX_LOCK_SECONDS``).

    Absent ou vide → ``None`` ; ``lock=0`` → ``None`` (pas de lock) ;
    invalide, négatif ou trop grand → 400 avec un message clair.
    """
    raw = request.query_params.get("lock")
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="lock doit être un entier de secondes entre 0 et 86400",
        ) from None
    if value < 0 or value > MAX_LOCK_SECONDS:
        raise HTTPException(
            status_code=400,
            detail="lock doit être un entier de secondes entre 0 et 86400",
        )
    return None if value == 0 else float(value)


@router.post("/dongles/reset", response_model=ResetBatchResult)
async def reset_dongles(
    body: ResetRequest, svc: ResetService = Depends(get_reset_service)
):
    results = await svc.reset_many(body.ids)
    return ResetBatchResult(
        results=[ResetBatchItem(dongle_id=did, status=o.status, message=o.message) for did, o in results]
    )


async def _trigger_reset(dongle_id: UUID, request: Request, svc: ResetService):
    """Déclenche le reset et journalise la méthode HTTP utilisée (sans secret)."""
    lock_seconds = _parse_lock_seconds(request)
    lock_detail = f" lock={lock_seconds:g}s" if lock_seconds is not None else ""
    logger.info("reset dongle %s déclenché via %s%s", dongle_id, request.method, lock_detail)
    return await svc.reset(dongle_id, lock_seconds=lock_seconds)


@router.post("/dongles/{dongle_id}/reset", response_model=ResetResult)
async def reset_dongle_post(
    dongle_id: UUID,
    request: Request,
    response: Response,
    svc: ResetService = Depends(get_reset_service),
):
    outcome = await _trigger_reset(dongle_id, request, svc)
    # Réponse à ne jamais mettre en cache (contient l'état d'une action admin).
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return ResetResult(status=outcome.status, message=outcome.message)


@router.get("/dongles/{dongle_id}/reset", response_class=PlainTextResponse)
async def reset_dongle_get(
    dongle_id: UUID,
    request: Request,
    svc: ResetService = Depends(get_reset_service),
):
    outcome = await _trigger_reset(dongle_id, request, svc)
    # Corps exact = statut métier brut (DONE/ERROR/PENDING/LOCK), sans newline,
    # HTTP 200 : les outils GET-only (Ankabot) comparent le corps à ``DONE``.
    # Le corps ne portant que le statut, le détail (raison du PENDING/LOCK/ERROR)
    # est journalisé ici pour ne pas perdre l'observabilité sur ce chemin.
    detail = f" ({outcome.message})" if outcome.message else ""
    logger.info("reset dongle %s via GET: %s%s", dongle_id, outcome.status, detail)
    return PlainTextResponse(
        content=outcome.status,
        status_code=200,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/dongles/{dongle_id}/unlock", response_class=PlainTextResponse)
async def unlock_dongle(
    dongle_id: UUID,
    request: Request,
    svc: ResetService = Depends(get_reset_service),
):
    """Lève le verrou manuel de reset (idempotent, action admin journalisée)."""
    outcome = await svc.unlock(dongle_id)
    detail = f" ({outcome.message})" if outcome.message else ""
    logger.info("déverrouillage dongle %s via %s: %s%s", dongle_id, request.method, outcome.status, detail)
    return PlainTextResponse(
        content=outcome.status,
        status_code=200,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
