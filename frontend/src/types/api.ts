export interface DiscoveredDongle {
  identity_imei: string | null;
  identity_usb_serial: string | null;
  identity_iccid: string | null;
  interface_name: string | null;
  suggested_label: string;
  suggested_apn: string | null;
}

export type DongleState = 
  | 'DISCOVERED' | 'UNENROLLED' | 'CONFIGURING' | 'CONNECTING' 
  | 'ONLINE' | 'DEGRADED' | 'RESETTING' | 'OFFLINE' | 'ERROR';

export type ProxyState = 
  | 'STOPPED' | 'STARTING' | 'RUNNING' | 'CRASHED' | 'REMOVED';

export interface DongleSummary {
  id: string;
  label: string;
  state: DongleState;
  interface_name: string;
  public_ip: string | null;
  signal_quality: number | null;
}

export interface DongleMetrics {
  signal_quality: {
    percent: number | null;
    rsrp: number | null;
    rsrq: number | null;
    sinr: number | null;
    rssi: number | null;
  } | null;
  ping: {
    host: string;
    packets_sent: number;
    packets_received: number;
    min_ms: number | null;
    avg_ms: number | null;
    max_ms: number | null;
  } | null;
  public_ip: string | null;
  proxy_health: 'healthy' | 'unhealthy' | 'unknown' | null;
  proxy_health_reason: string | null;
}

export interface DongleDetail extends DongleSummary {
  identity_imei: string | null;
  identity_usb_serial: string | null;
  identity_iccid: string | null;
  apn: string;
  apn_auth: string | null;
  route_table_id: number;
  fwmark: number;
  proxy_id: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  metrics: DongleMetrics | null;
}

export interface EnrollmentProxyCredentials {
  username: string;
  password: string;
}

export interface DongleEnrollmentResponse extends DongleDetail {
  proxy_credentials: EnrollmentProxyCredentials | null;
}

export interface ProxyDetail {
  id: string;
  dongle_id: string | null;
  source_type: string;
  source_ref: string | null;
  interface_name: string | null;
  container_name: string;
  listen_port: number;
  auth_user: string | null;
  state: ProxyState;
  enabled: boolean;
  network_ip: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProxyWithPassword {
  proxy: ProxyDetail;
  password: string | null;
}

export interface AllowedHost {
  host: string;
}

export type LocalInterfaceKind = 'ethernet' | 'wifi' | 'other';

export interface LocalConnection {
  interface_name: string;
  ip: string | null;
  kind: LocalInterfaceKind;
  enrolled: boolean;
  proxy_id: string | null;
}

export interface ProxyListenInterfaceSetting {
  interface_name: string | null;
}

export type ResetStatus = 'DONE' | 'ERROR' | 'PENDING';

export interface ResetResponse {
  status: ResetStatus;
  message: string | null;
}

export interface ResetBatchItem {
  dongle_id: string;
  status: ResetStatus;
  message: string | null;
}

export interface ResetBatchResponse {
  results: ResetBatchItem[];
}
