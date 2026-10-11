"""Résolution d'APN à partir de l'identité SIM/réseau.

Domaine pur : aucune I/O, aucun sous-processus. Le jeu d'APN provient d'une
source externe (ex. base MBPI) et est injecté sous forme de ``NetworkProvider``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ApnEntry:
    apn: str
    username: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class NetworkProvider:
    name: str
    mcc: str
    mnc: str
    apns: tuple[ApnEntry, ...] = ()


@dataclass(frozen=True)
class ApnSuggestion:
    apn: str
    operator: str | None
    source: str = "mbpi"


@runtime_checkable
class ApnResolver(Protocol):
    """Résout un APN plausible pour une identité réseau donnée."""

    def resolve(
        self,
        imsi: str | None,
        operator_id: str | None,
        operator_name: str | None,
    ) -> ApnSuggestion | None:
        ...


# MBPI fournit le jeu d'APN « internet » par opérateur. Cette liste ordonnée
# privilégie les APN génériques grand public connus en France lorsque plusieurs
# candidats coexistent pour un même réseau (ex. contrats pro vs grand public).
_PREFERRED_APNS = (
    "free",
    "orange",
    "websfr",
    "mmsbouygtel.com",
    "sl2sfr",
    "orange.fr",
    "ebouygtel.com",
)


def candidate_network_ids(
    imsi: str | None, operator_id: str | None
) -> list[tuple[str, str]]:
    """Couples (mcc, mnc) candidats, dédupliqués et ordonnés.

    ``operator_id`` est examiné avant ``imsi``. Pour chaque chaîne numérique
    d'au moins 5 chiffres, on propose un MNC à 2 chiffres puis, si possible,
    un MNC à 3 chiffres.
    """
    candidates: list[tuple[str, str]] = []
    for raw in (operator_id, imsi):
        if not raw or not raw.isdigit() or len(raw) < 5:
            continue
        candidates.append((raw[:3], raw[3:5]))
        if len(raw) >= 6:
            candidates.append((raw[:3], raw[3:6]))

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _normalize_name(value: str | None) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _name_matches(operator_name: str | None, provider_name: str) -> bool:
    haystack = _normalize_name(operator_name)
    needle = _normalize_name(provider_name)
    if not haystack or not needle:
        return False
    return needle in haystack or haystack in needle


def _pick_apn(provider: NetworkProvider) -> str | None:
    values = [entry.apn for entry in provider.apns]
    for preferred in _PREFERRED_APNS:
        if preferred in values:
            return preferred
    return values[0] if values else None


def _select_provider(
    providers: Sequence[NetworkProvider], operator_name: str | None
) -> NetworkProvider | None:
    if not providers:
        return None
    if operator_name:
        for provider in providers:
            if _name_matches(operator_name, provider.name):
                return provider
    return providers[0]


def _index(
    providers: Sequence[NetworkProvider],
) -> dict[tuple[str, str], list[NetworkProvider]]:
    index: dict[tuple[str, str], list[NetworkProvider]] = {}
    for provider in providers:
        index.setdefault((provider.mcc, provider.mnc), []).append(provider)
    return index


def resolve_apn(
    imsi: str | None,
    operator_id: str | None,
    operator_name: str | None,
    providers: Sequence[NetworkProvider],
) -> ApnSuggestion | None:
    """Retourne l'APN le plus plausible pour l'identité fournie.

    Priorité : correspondance exacte (mcc, mnc) — départagée par le nom
    d'opérateur si plusieurs fournisseurs partagent le réseau — puis repli sur
    le nom d'opérateur seul. ``None`` si rien ne correspond.
    """
    index = _index(providers)
    for network_id in candidate_network_ids(imsi, operator_id):
        provider = _select_provider(index.get(network_id, ()), operator_name)
        if provider is None:
            continue
        apn = _pick_apn(provider)
        if apn:
            return ApnSuggestion(apn=apn, operator=provider.name)

    if operator_name:
        for provider in providers:
            if _name_matches(operator_name, provider.name):
                apn = _pick_apn(provider)
                if apn:
                    return ApnSuggestion(apn=apn, operator=provider.name)
    return None
