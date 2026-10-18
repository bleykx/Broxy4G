from uuid import UUID

from pydantic import BaseModel


class DongleCreate(BaseModel):
    identity_imei: str | None = None
    identity_usb_serial: str | None = None
    identity_iccid: str | None = None
    label: str
    apn: str | None = None
    transport: str = "modem"
    identity_adb_serial: str | None = None


class DongleUpdate(BaseModel):
    label: str | None = None
    apn: str | None = None
    apn_auth: str | None = None


class SignalQualityDTO(BaseModel):
    percent: int | None = None
    rsrp: int | None = None
    rsrq: int | None = None
    sinr: int | None = None
    rssi: int | None = None


class PingResultDTO(BaseModel):
    host: str
    packets_sent: int
    packets_received: int
    min_ms: float | None = None
    avg_ms: float | None = None
    max_ms: float | None = None


class DongleMetricsDTO(BaseModel):
    signal_quality: SignalQualityDTO | None = None
    ping: PingResultDTO | None = None
    public_ip: str | None = None
    proxy_health: str | None = None
    proxy_health_reason: str | None = None


class DiscoveredDongle(BaseModel):
    identity_imei: str | None = None
    identity_usb_serial: str | None = None
    identity_iccid: str | None = None
    interface_name: str | None = None
    suggested_label: str
    suggested_apn: str | None = None
    transport: str = "modem"
    identity_adb_serial: str | None = None
    model: str | None = None


class DongleSummary(BaseModel):
    id: UUID
    label: str
    state: str
    interface_name: str | None = None
    public_ip: str | None = None
    signal_quality: int | None = None
    transport: str = "modem"


class DongleDetail(BaseModel):
    id: UUID
    label: str
    state: str
    identity_imei: str | None = None
    identity_usb_serial: str | None = None
    identity_iccid: str | None = None
    identity_adb_serial: str | None = None
    apn: str | None = None
    apn_auth: str | None = None
    route_table_id: int | None = None
    fwmark: int | None = None
    interface_name: str | None = None
    proxy_id: UUID | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str
    metrics: DongleMetricsDTO | None = None
    transport: str = "modem"
