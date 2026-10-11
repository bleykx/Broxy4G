"""Source d'APN basée sur la base MBPI (mobile-broadband-provider-info).

Lecture du fichier ``serviceproviders.xml`` (licence CC-PD, vendored dans
``broxy4g/data``) et résolution d'un APN plausible pour une identité réseau.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path

from broxy4g.domain.apn_resolution import (
    ApnEntry,
    ApnSuggestion,
    NetworkProvider,
    resolve_apn,
)

DEFAULT_MBPI_PATH = Path(__file__).resolve().parent.parent / "data" / "serviceproviders.xml"


def _read_apns(gsm: ET.Element) -> tuple[ApnEntry, ...]:
    all_entries: list[ApnEntry] = []
    internet_entries: list[ApnEntry] = []
    for apn in gsm.findall("apn"):
        value = apn.get("value")
        if not value:
            continue
        entry = ApnEntry(
            apn=value,
            username=apn.findtext("username") or None,
            password=apn.findtext("password") or None,
        )
        all_entries.append(entry)
        usages = {usage.get("type") for usage in apn.findall("usage")}
        if "internet" in usages:
            internet_entries.append(entry)
    return tuple(internet_entries or all_entries)


@cache
def load_providers(path: str | None = None) -> tuple[NetworkProvider, ...]:
    """Charge les fournisseurs (mcc, mnc, APN) depuis un fichier MBPI.

    Le chemin par défaut pointe sur le fichier vendored. Le résultat est mis en
    cache (clé = chemin).
    """
    providers_path = Path(path) if path else DEFAULT_MBPI_PATH
    root = ET.parse(providers_path).getroot()

    providers: list[NetworkProvider] = []
    for country in root.findall("country"):
        for provider in country.findall("provider"):
            gsm = provider.find("gsm")
            if gsm is None:
                continue
            name = provider.findtext("name") or ""
            apns = _read_apns(gsm)
            for network_id in gsm.findall("network-id"):
                mcc = network_id.get("mcc")
                mnc = network_id.get("mnc")
                if not mcc or not mnc:
                    continue
                providers.append(
                    NetworkProvider(name=name, mcc=mcc, mnc=mnc, apns=apns)
                )
    return tuple(providers)


class MbpiApnSource:
    """Résout un APN plausible depuis l'IMSI / l'opérateur (base MBPI)."""

    def __init__(self, path: str | None = None) -> None:
        self._providers = load_providers(path)

    def resolve(
        self,
        imsi: str | None,
        operator_id: str | None,
        operator_name: str | None,
    ) -> ApnSuggestion | None:
        return resolve_apn(imsi, operator_id, operator_name, self._providers)
