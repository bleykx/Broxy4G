"""Boucle d'attente bornée de la propagation de l'IP publique après un reset.

Le reset renvoie ``DONE`` seulement si le verdict de la sonde d'IP publique
(épinglée à l'IP source du bearer) atteint ``OK``. Comme l'IP publique peut
laguer de quelques secondes derrière le bearer, cette boucle re-sonde un
nombre d'essais fini, à intervalle fixe.
"""

import asyncio
import logging
from uuid import UUID

from broxy4g.domain.degraded_arbiter import ProbeVerdict
from broxy4g.services.connection import ConnectionService
from broxy4g.services.uplink_probe import PublicIpVerdict

logger = logging.getLogger(__name__)


async def await_public_ip(
    connection: ConnectionService,
    dongle_id: UUID,
    initial: PublicIpVerdict,
    attempts: int,
    delay: float,
) -> PublicIpVerdict:
    """Attend la propagation de l'IP publique (verdict de sonde ``OK``)."""
    probe = initial
    for _ in range(attempts):
        if probe.verdict is ProbeVerdict.OK:
            return probe
        await asyncio.sleep(delay)
        try:
            probe = await connection.get_public_ip_verdict(dongle_id)
        except Exception:
            logger.warning("Sonde IP publique indécidable après reset dongle %s", dongle_id)
            probe = PublicIpVerdict(ProbeVerdict.FAILED, None)
    return probe
