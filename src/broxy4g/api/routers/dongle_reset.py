"""Routes de reset de dongle (batch POST + single POST/GET).

Le single reset est aussi exposé en GET pour les outils externes GET-only
(Ankabot) qui ne peuvent passer qu'une URL. La logique métier reste celle du
``ResetService`` (idempotence, verrou par dongle, statuts DONE/ERROR/PENDING).
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

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


@router.get("/dongles/{dongle_id}/reset", response_model=ResetResult)
@router.post("/dongles/{dongle_id}/reset", response_model=ResetResult)
async def reset_dongle(
    dongle_id: UUID,
    request: Request,
    response: Response,
    svc: ResetService = Depends(get_reset_service),
):
    # Le reset via GET est une action d'administration déclenchée par URL :
    # on journalise la méthode utilisée (sans aucun secret).
    logger.info("reset dongle %s déclenché via %s", dongle_id, request.method)
    outcome = await svc.reset(dongle_id)
    # Réponse à ne jamais mettre en cache (contient l'état d'une action admin).
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return ResetResult(status=outcome.status, message=outcome.message)
