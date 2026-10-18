from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from broxy4g.api.deps import (
    get_connection_service,
    get_discovery_service,
    get_enrollment_service,
    get_metrics_snapshot,
    get_proxy_health_service,
    require_auth,
)
from broxy4g.api.routers.dongle_presenters import (
    to_detail,
    to_discovered_dongle,
    to_enrollment_response,
    to_summary,
    validate_enrollment,
)
from broxy4g.api.schemas.dongle import (
    DiscoveredDongle,
    DongleCreate,
    DongleDetail,
    DongleSummary,
    DongleUpdate,
)
from broxy4g.api.schemas.dongle_enrollment import DongleEnrollmentResponse
from broxy4g.services.connection import ConnectionService
from broxy4g.services.discovery import DiscoveryService
from broxy4g.services.enrollment import EnrollmentService
from broxy4g.services.metrics_snapshot import MetricsSnapshot
from broxy4g.services.proxy_health import ProxyHealthService

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/dongles", response_model=list[DongleSummary])
async def list_dongles(
    svc: EnrollmentService = Depends(get_enrollment_service),
    snapshot: MetricsSnapshot = Depends(get_metrics_snapshot),
):
    dongles = await svc.list_all()
    return [to_summary(d, snapshot.get(d.id)) for d in dongles]


@router.get("/dongles/discovered", response_model=list[DiscoveredDongle])
async def list_discovered_dongles(svc: DiscoveryService = Depends(get_discovery_service)):
    discovered = await svc.list_unenrolled()
    return [to_discovered_dongle(d) for d in discovered]


@router.post("/dongles", response_model=DongleEnrollmentResponse, status_code=status.HTTP_201_CREATED)
async def enroll_dongle(
    body: DongleCreate, svc: EnrollmentService = Depends(get_enrollment_service)
):
    transport, identity = validate_enrollment(body)
    try:
        result = await svc.enroll(identity, body.label, body.apn, transport=transport)
    except ValueError as e:
        code = 409 if "already enrolled" in str(e) else 422
        raise HTTPException(status_code=code, detail=str(e))
    return to_enrollment_response(result)


@router.get("/dongles/{dongle_id}", response_model=DongleDetail)
async def get_dongle(
    dongle_id: UUID,
    svc: EnrollmentService = Depends(get_enrollment_service),
    conn: ConnectionService = Depends(get_connection_service),
    health_svc: ProxyHealthService = Depends(get_proxy_health_service),
):
    dongle = await svc.get_by_id(dongle_id)
    if not dongle:
        raise HTTPException(status_code=404, detail="Dongle not found")
    metrics = await conn.get_metrics(dongle_id)
    health = await health_svc.for_dongle(dongle_id)
    metrics.proxy_health = health.status
    return to_detail(dongle, metrics, health)


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
    return to_detail(dongle)


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
