"""Politique d'échec du reset : marquer ``ERROR`` seulement si l'uplink est cassé.

Motif : marquer ``ERROR`` un dongle dont le lien fonctionne déclenche une
ré-énumération USB destructive (``usb_recovery``). Si la sonde d'IP publique
(le même signal que le gate ``DONE``) est ``OK``, l'état du dongle est
conservé et seul ``last_error`` est renseigné.
"""

import logging
from uuid import UUID

from broxy4g.domain.degraded_arbiter import ProbeVerdict
from broxy4g.domain.dongle_states import DongleState, can_transition
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.services.connection import ConnectionService

logger = logging.getLogger(__name__)


async def uplink_broken(connection: ConnectionService, dongle_id: UUID) -> bool:
    """``True`` si l'uplink est cassé : marquage ``ERROR`` autorisé.

    Un seul appel de sonde (pas de boucle d'attente) : l'échec doit rester
    rapide. Une sonde indécidable (qui lève) est traitée comme cassée
    (fail-safe) : la récupération prévaut sur l'état conservé.
    """
    try:
        probe = await connection.get_public_ip_verdict(dongle_id)
    except Exception:
        logger.warning("Sonde IP publique indécidable après reset dongle %s", dongle_id)
        return True
    return probe.verdict is not ProbeVerdict.OK


async def record_reset_failure(
    dongle_repo: DongleRepository,
    dongle_id: UUID,
    message: str,
    mark_error: bool,
) -> None:
    """Écrit ``last_error`` et, si ``mark_error``, passe le dongle en ``ERROR``.

    Toute erreur de persistance est avalée (log) : l'appelant doit de toute
    façon retourner ``ERROR``.
    """
    try:
        dongle = await dongle_repo.get_by_id(dongle_id)
        if dongle is None:
            return
        dongle.last_error = message
        if mark_error and can_transition(dongle.state, DongleState.ERROR):
            dongle.change_state(DongleState.ERROR)
        await dongle_repo.save(dongle)
    except Exception:
        logger.exception("Échec de la récupération d'état après reset dongle %s", dongle_id)
