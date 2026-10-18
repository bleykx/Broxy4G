"""Mappers domaine → DTO et validation de la charge d'enrôlement du routeur /dongles."""
from fastapi import HTTPException, status

from broxy4g.api.schemas.dongle import (
    DiscoveredDongle,
    DongleCreate,
    DongleDetail,
    DongleMetricsDTO,
    DongleSummary,
    PingResultDTO,
    SignalQualityDTO,
)
from broxy4g.api.schemas.dongle_enrollment import (
    DongleEnrollmentResponse,
    EnrollmentProxyCredentials,
)
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.transport import Transport
from broxy4g.services.discovery import DiscoveredDongle as ServiceDiscoveredDongle
from broxy4g.services.enrollment import EnrollmentResult
from broxy4g.services.metrics_snapshot import SummaryMetrics
from broxy4g.services.proxy_health import ProxyHealth


def to_summary(d: Dongle, metrics: SummaryMetrics | None = None) -> DongleSummary:
    return DongleSummary(
        id=d.id,
        label=d.label,
        state=d.state.value,
        interface_name=d.interface_name,
        public_ip=metrics.public_ip if metrics else None,
        signal_quality=metrics.signal_quality if metrics else None,
        transport=d.transport.value,
    )


def to_detail(d: Dongle, metrics=None, health: ProxyHealth | None = None) -> DongleDetail:
    metrics_dto = None
    if metrics:
        metrics_dto = DongleMetricsDTO(
            signal_quality=SignalQualityDTO(**metrics.signal_quality.__dict__) if metrics.signal_quality else None,
            ping=PingResultDTO(**metrics.ping.__dict__) if metrics.ping else None,
            public_ip=metrics.public_ip,
            proxy_health=metrics.proxy_health,
            proxy_health_reason=health.reason if health else None,
        )
    return DongleDetail(
        id=d.id,
        label=d.label,
        state=d.state.value,
        identity_imei=d.identity.imei,
        identity_usb_serial=d.identity.usb_serial,
        identity_iccid=d.identity.iccid,
        identity_adb_serial=d.identity.adb_serial,
        apn=d.apn,
        apn_auth=d.apn_auth,
        route_table_id=d.route_table_id,
        fwmark=d.fwmark,
        interface_name=d.interface_name,
        proxy_id=d.proxy_id,
        last_error=d.last_error,
        created_at=d.created_at.isoformat(),
        updated_at=d.updated_at.isoformat(),
        metrics=metrics_dto,
        transport=d.transport.value,
    )


def to_enrollment_response(result: EnrollmentResult) -> DongleEnrollmentResponse:
    """Construit la réponse one-time de l'enrôlement (identifiants le cas échéant)."""
    credentials = result.proxy_credentials
    return DongleEnrollmentResponse(
        **to_detail(result.dongle).model_dump(),
        proxy_credentials=(
            EnrollmentProxyCredentials(username=credentials.username, password=credentials.password)
            if credentials is not None
            else None
        ),
    )


def to_discovered_dongle(d: ServiceDiscoveredDongle) -> DiscoveredDongle:
    return DiscoveredDongle(
        identity_imei=d.identity_imei,
        identity_usb_serial=d.identity_usb_serial,
        identity_iccid=d.identity_iccid,
        interface_name=d.interface_name,
        suggested_label=d.suggested_label,
        suggested_apn=d.suggested_apn,
        transport=d.transport,
        identity_adb_serial=d.identity_adb_serial,
        model=d.model,
    )


def parse_transport(raw: str) -> Transport:
    """Analyse le transport fourni ; valeur inconnue → 422 lisible, jamais de 500."""
    value = (raw or "modem").strip().lower()
    if value not in ("modem", "adb"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"transport inconnu : « {raw} » (valeurs acceptées : modem, adb)",
        )
    return Transport.from_value(value)


def validate_enrollment(body: DongleCreate) -> tuple[Transport, IdentityMaterial]:
    """Valide la charge d'enrôlement : au moins un identifiant, cohérence transport/ADB."""
    transport = parse_transport(body.transport)
    has_identity = any(
        (body.identity_imei, body.identity_usb_serial, body.identity_iccid, body.identity_adb_serial)
    )
    if not has_identity:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="aucun identifiant matériel fourni (IMEI, numéro de série USB, ICCID ou numéro de série ADB)",
        )
    if transport is Transport.ADB and not body.identity_adb_serial:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="le transport adb nécessite un numéro de série ADB (identity_adb_serial)",
        )
    if transport is Transport.MODEM and body.identity_adb_serial:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="identity_adb_serial n'est pas autorisé avec le transport modem",
        )
    return transport, IdentityMaterial(
        imei=body.identity_imei,
        usb_serial=body.identity_usb_serial,
        iccid=body.identity_iccid,
        adb_serial=body.identity_adb_serial,
    )
