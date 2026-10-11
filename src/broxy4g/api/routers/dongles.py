from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from broxy4g.api.deps import (
    get_connection_service,
    get_discovery_service,
    get_enrollment_service,
    get_metrics_snapshot,
    get_reset_service,
    require_auth,
)
from broxy4g.api.schemas.dongle import (
    DiscoveredDongle,
    DongleCreate,
    DongleDetail,
    DongleMetricsDTO,
    DongleSummary,
    DongleUpdate,
    PingResultDTO,
    SignalQualityDTO,
)
from broxy4g.api.schemas.reset import (
    ResetBatchItem,
    ResetBatchResult,
    ResetRequest,
    ResetResult,
)
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.services.connection import ConnectionService
from broxy4g.services.discovery import DiscoveredDongle as ServiceDiscoveredDongle
from broxy4g.services.discovery import DiscoveryService
from broxy4g.services.enrollment import EnrollmentService
from broxy4g.services.metrics_snapshot import MetricsSnapshot, SummaryMetrics
from broxy4g.services.reset import ResetService

router = APIRouter(dependencies=[Depends(require_auth)])


def _to_summary(d: Dongle, metrics: SummaryMetrics | None = None) -> DongleSummary:
    return DongleSummary(
        id=d.id,
        label=d.label,
        state=d.state.value,
        interface_name=d.interface_name,
        public_ip=metrics.public_ip if metrics else None,
        signal_quality=metrics.signal_quality if metrics else None,
    )


def _to_detail(d: Dongle, metrics=None) -> DongleDetail:
    metrics_dto = None
    if metrics:
        metrics_dto = DongleMetricsDTO(
            signal_quality=SignalQualityDTO(**metrics.signal_quality.__dict__) if metrics.signal_quality else None,
            ping=PingResultDTO(**metrics.ping.__dict__) if metrics.ping else None,
            public_ip=metrics.public_ip,
            proxy_health=metrics.proxy_health,
        )
    return DongleDetail(
        id=d.id,
        label=d.label,
        state=d.state.value,
        identity_imei=d.identity.imei,
        identity_usb_serial=d.identity.usb_serial,
        identity_iccid=d.identity.iccid,
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
    )


def _to_discovered_dongle(d: ServiceDiscoveredDongle) -> DiscoveredDongle:
    return DiscoveredDongle(
        identity_imei=d.identity_imei,
        identity_usb_serial=d.identity_usb_serial,
        identity_iccid=d.identity_iccid,
        interface_name=d.interface_name,
        suggested_label=d.suggested_label,
        suggested_apn=d.suggested_apn,
    )


@router.get("/dongles", response_model=list[DongleSummary])
async def list_dongles(
    svc: EnrollmentService = Depends(get_enrollment_service),
    snapshot: MetricsSnapshot = Depends(get_metrics_snapshot),
):
    dongles = await svc.list_all()
    return [_to_summary(d, snapshot.get(d.id)) for d in dongles]


@router.get("/dongles/discovered", response_model=list[DiscoveredDongle])
async def list_discovered_dongles(svc: DiscoveryService = Depends(get_discovery_service)):
    discovered = await svc.list_unenrolled()
    return [_to_discovered_dongle(d) for d in discovered]


@router.post("/dongles", response_model=DongleDetail, status_code=status.HTTP_201_CREATED)
async def enroll_dongle(
    body: DongleCreate, svc: EnrollmentService = Depends(get_enrollment_service)
):
    identity = IdentityMaterial(
        imei=body.identity_imei, usb_serial=body.identity_usb_serial, iccid=body.identity_iccid
    )
    try:
        dongle = await svc.enroll(identity, body.label, body.apn)
    except ValueError as e:
        code = 409 if "already enrolled" in str(e) else 422
        raise HTTPException(status_code=code, detail=str(e))
    return _to_detail(dongle)


@router.post("/dongles/reset", response_model=ResetBatchResult)
async def reset_dongles(
    body: ResetRequest, svc: ResetService = Depends(get_reset_service)
):
    results = await svc.reset_many(body.ids)
    return ResetBatchResult(
        results=[ResetBatchItem(dongle_id=did, status=o.status, message=o.message) for did, o in results]
    )


@router.get("/dongles/{dongle_id}", response_model=DongleDetail)
async def get_dongle(
    dongle_id: UUID,
    svc: EnrollmentService = Depends(get_enrollment_service),
    conn: ConnectionService = Depends(get_connection_service),
):
    dongle = await svc.get_by_id(dongle_id)
    if not dongle:
        raise HTTPException(status_code=404, detail="Dongle not found")
    metrics = await conn.get_metrics(dongle_id)
    return _to_detail(dongle, metrics)


@router.patch("/dongles/{dongle_id}", response_model=DongleDetail)
async def update_dongle(
    dongle_id: UUID,
    body: DongleUpdate,
    svc: EnrollmentService = Depends(get_enrollment_service),
):
    try:
        dongle = await svc.update(dongle_id, body.label, body.apn, body.apn_auth)
    except ValueError:
        raise HTTPException(status_code=404, detail="Dongle not found")
    return _to_detail(dongle)


@router.delete("/dongles/{dongle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_dongle(
    dongle_id: UUID, svc: EnrollmentService = Depends(get_enrollment_service)
):
    try:
        await svc.deactivate(dongle_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Dongle not found")


@router.post("/dongles/{dongle_id}/connect", status_code=status.HTTP_202_ACCEPTED)
async def connect_dongle(
    dongle_id: UUID, conn: ConnectionService = Depends(get_connection_service)
):
    try:
        await conn.connect(dongle_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Dongle not found")


@router.post("/dongles/{dongle_id}/disconnect", status_code=status.HTTP_202_ACCEPTED)
async def disconnect_dongle(
    dongle_id: UUID, conn: ConnectionService = Depends(get_connection_service)
):
    try:
        await conn.disconnect(dongle_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Dongle not found")


@router.post("/dongles/{dongle_id}/reset", response_model=ResetResult)
async def reset_dongle(
    dongle_id: UUID, svc: ResetService = Depends(get_reset_service)
):
    outcome = await svc.reset(dongle_id)
    return ResetResult(status=outcome.status, message=outcome.message)
