"""Sélection déterministe d'un membre de pool par connexion.

Le membre retenu est celui qui a le moins de connexions en cours, puis le moins
récemment utilisé, puis l'ordre stable des ``proxy_id``. Le choix est fait en
mémoire (gateway) : aucune I/O, aucune persistance, aucune mutation des
mappings passés en paramètre. Les mots de passe sont clairs et transitoires :
ils ne doivent jamais être journalisés.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class PoolCandidate:
    """Membre éligible proposé à la sélection.

    ``username``/``password`` sont ceux du proxy membre (jamais ceux du pool),
    clairs et transitoires : jamais loggués ni journalisés.
    """

    proxy_id: UUID
    host: str
    port: int
    username: str
    password: str


def pick_candidate(
    candidates: Sequence[PoolCandidate],
    *,
    in_flight: Mapping[UUID, int] | None = None,
    last_used: Mapping[UUID, float] | None = None,
) -> PoolCandidate | None:
    """Choisir le meilleur membre parmi les candidats.

    Critère trié par croissant : nombre de connexions en cours, horodatage du
    dernier usage, puis ``str(proxy_id)`` pour une stabilité totale. Retourne
    ``None`` si la liste est vide.
    """
    if not candidates:
        return None
    in_flight = in_flight or {}
    last_used = last_used or {}
    return min(
        candidates,
        key=lambda c: (in_flight.get(c.proxy_id, 0), last_used.get(c.proxy_id, 0.0), str(c.proxy_id)),
    )
