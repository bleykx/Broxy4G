"""Routes de reset de dongle (batch POST + single POST/GET).

Le single reset est aussi exposé en GET pour les outils externes GET-only
(Ankabot) qui ne peuvent passer qu'une URL. La logique métier reste celle du
``ResetService`` (idempotence, verrou par dongle, statuts DONE/ERROR/PENDING).
La variante GET renvoie le statut métier brut en ``text/plain`` (corps exact,
sans newline, HTTP 200) : Ankabot compare le corps de la réponse à la valeur
``DONE``.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse

from broxy4g.api.deps import get_reset_service, require_auth
from broxy4g.api.schemas.reset import (
    ResetBatchItem,
    ResetBatchResult,
    ResetRequest,
    ResetResult,
)
from broxy4g.services.reset import ResetService

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])


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
    logger.info("reset dongle %s déclenché via %s", dongle_id, request.method)
    return await svc.reset(dongle_id)


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
    # Corps exact = statut métier brut (DONE/ERROR/PENDING), sans newline,
    # HTTP 200 : les outils GET-only (Ankabot) comparent le corps à ``DONE``.
    # Le corps ne portant que le statut, le détail (raison du PENDING/ERROR)
    # est journalisé ici pour ne pas perdre l'observabilité sur ce chemin.
    detail = f" ({outcome.message})" if outcome.message else ""
    logger.info("reset dongle %s via GET: %s%s", dongle_id, outcome.status, detail)
    return PlainTextResponse(
        content=outcome.status,
        status_code=200,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
