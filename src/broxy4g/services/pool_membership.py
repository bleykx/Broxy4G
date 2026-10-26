"""Éligibilité des membres d'un pool et production des candidats de sélection.

Un membre est éligible selon des règles métier exactes, évaluées dans un ordre
fixe : une seule raison de refus, la première qui s'applique. Aucun mot de
passe ne figure dans les journaux ni dans une ``repr`` : ``PoolCandidate``
porte le mot de passe clair du proxy, on ne journalise donc jamais l'objet,
seulement le ``proxy_id``. Un membre cassé (lecture impossible) est exclu sans
faire fuiter d'exception.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.pool_selection import PoolCandidate
from broxy4g.domain.proxy import Proxy, ProxySource, ProxyState
from broxy4g.domain.proxy_config import INTERNAL_PROXY_PORT
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.pool_repository import PoolRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.proxy_secret_box import ProxySecretBox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemberEvaluation:
    """Verdict d'éligibilité d'un membre (une seule raison, la première)."""

    proxy_id: UUID
    eligible: bool
    reason: str | None
    candidate: PoolCandidate | None
    # Nom lisible du membre : label du dongle, ou nom d'interface pour un uplink
    # local. Jamais un identifiant technique quand un nom existe.
    label: str | None = None


def _excluded(proxy_id: UUID, reason: str, label: str | None = None) -> MemberEvaluation:
    return MemberEvaluation(
        proxy_id=proxy_id, eligible=False, reason=reason, candidate=None, label=label
    )


def _included(proxy: Proxy, password: str, label: str) -> MemberEvaluation:
    """Candidat éligible : ``network_ip`` et ``auth_user`` vérifiés non vides avant."""
    candidate = PoolCandidate(
        proxy_id=proxy.id,
        host=proxy.network_ip,
        port=INTERNAL_PROXY_PORT,
        username=proxy.auth_user,
        password=password,
    )
    return MemberEvaluation(
        proxy_id=proxy.id, eligible=True, reason=None, candidate=candidate, label=label
    )


class PoolMembershipService:
    """Évalue les membres d'un pool et produit leurs candidats de sélection."""

    def __init__(
        self,
        pool_repo: PoolRepository,
        proxy_repo: ProxyRepository,
        dongle_repo: DongleRepository,
        secret_box: ProxySecretBox,
        reset_lock=None,
    ):
        self._pool_repo = pool_repo
        self._proxy_repo = proxy_repo
        self._dongle_repo = dongle_repo
        self._secret_box = secret_box
        self._reset_lock = reset_lock

    async def evaluate(self, pool_id: UUID) -> list[MemberEvaluation]:
        """Verdict par membre, dans l'ordre stable de ``list_member_ids``."""
        evaluations = []
        for proxy_id in await self._pool_repo.list_member_ids(pool_id):
            evaluations.append(await self._evaluate_member(proxy_id))
        return evaluations

    async def candidates(self, pool_id: UUID) -> list[PoolCandidate]:
        """Candidats éligibles (ordre des membres), prêts pour la sélection."""
        return [
            evaluation.candidate
            for evaluation in await self.evaluate(pool_id)
            if evaluation.candidate is not None
        ]

    async def _evaluate_member(self, proxy_id: UUID) -> MemberEvaluation:
        try:
            proxy = await self._proxy_repo.get_by_id(proxy_id)
            if proxy is None:
                return _excluded(proxy_id, "proxy absent")
            label = proxy.source_ref  # uplink local : nom d'interface
            if not proxy.enabled:
                return _excluded(proxy_id, "proxy désactivé", label)
            if proxy.state is not ProxyState.RUNNING:
                return _excluded(proxy_id, f"proxy non démarré (state={proxy.state.value})", label)
            if not proxy.network_ip:
                return _excluded(proxy_id, "adresse interne inconnue", label)
            password = self._secret_box.decrypt(proxy.password_encrypted)
            if password is None or not proxy.auth_user:
                return _excluded(proxy_id, "identifiants non récupérables", label)
            if proxy.source_type is ProxySource.LOCAL:
                return _included(proxy, password, label or str(proxy.id))
            return await self._evaluate_dongle(proxy, password)
        except Exception:  # un membre cassé ne doit pas casser l'évaluation
            logger.exception("Membre %s illisible : exclu du pool", proxy_id)
            return _excluded(proxy_id, "membre illisible")

    async def _evaluate_dongle(self, proxy: Proxy, password: str) -> MemberEvaluation:
        dongle = await self._dongle_repo.get_by_id(proxy.dongle_id) if proxy.dongle_id else None
        if dongle is None:
            return _excluded(proxy.id, "dongle absent", proxy.source_ref)
        resetting = (
            dongle.state is DongleState.RESETTING
            or (
                self._reset_lock is not None
                and self._reset_lock.is_locked(str(dongle.id))
            )
        )
        if resetting:
            return _excluded(proxy.id, "dongle en reset", dongle.label)
        if dongle.state not in (DongleState.ONLINE, DongleState.DEGRADED):
            return _excluded(
                proxy.id, f"dongle non connecté (state={dongle.state.value})", dongle.label
            )
        return _included(proxy, password, dongle.label)
