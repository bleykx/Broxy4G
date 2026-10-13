import {
  DongleSummary,
  DongleDetail,
  DongleEnrollmentResponse,
  ProxyDetail,
  ProxyWithPassword,
  ResetResponse,
  ResetBatchResponse,
  DiscoveredDongle,
  LocalConnection,
  LocalConnectionView,
  AllowedHost,
  ProxyListenInterfaceSetting
} from '../types/api';

const API_BASE = '/api/v1';

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const credentials = sessionStorage.getItem('broxy4g_auth');
  
  const headers = new Headers(options.headers);
  if (credentials) {
    headers.set('Authorization', `Basic ${credentials}`);
  }
  headers.set('Content-Type', 'application/json');

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('broxy4g-auth-required'));
    throw new Error('UNAUTHORIZED');
  }

  if (!response.ok) {
    const errorData: unknown = await response.json().catch(() => ({}));
    let message = `API Error: ${response.status}`;

    const detail = typeof errorData === 'object' && errorData !== null
      ? (errorData as { detail?: unknown }).detail
      : undefined;
    if (typeof detail === 'string') {
      message = detail;
    } else if (Array.isArray(detail)) {
      message = detail.map(item =>
        typeof item === 'object' && item !== null && 'msg' in item
          ? String((item as { msg: unknown }).msg)
          : String(item),
      ).join('; ');
    }

    throw new Error(message);
  }

  if (response.status === 204) {
    return {} as T;
  }

  try {
    return await response.json();
  } catch {
    return {} as T;
  }
}

export const api = {
  health: {
    get: () => request<{ status: string }>('/health'),
    getVersion: () => request<{ version: string }>('/version'),
  },
  dongles: {
    list: () => request<DongleSummary[]>('/dongles'),
    discovered: () => request<DiscoveredDongle[]>('/dongles/discovered'),
    get: (id: string) => request<DongleDetail>(`/dongles/${id}`),
    create: (data: Partial<DongleDetail>) =>
      request<DongleEnrollmentResponse>('/dongles', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: string, data: Partial<DongleDetail>) => 
      request<DongleDetail>(`/dongles/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    delete: (id: string) => 
      request<void>(`/dongles/${id}`, { method: 'DELETE' }),
    connect: (id: string) => 
      request<void>(`/dongles/${id}/connect`, { method: 'POST' }),
    disconnect: (id: string) => 
      request<void>(`/dongles/${id}/disconnect`, { method: 'POST' }),
    reset: (id: string) => 
      request<ResetResponse>(`/dongles/${id}/reset`, { method: 'POST' }),
    resetMany: (ids: string[]) =>
      request<ResetBatchResponse>('/dongles/reset', {
        method: 'POST',
        body: JSON.stringify({ ids }),
      }),
  },
  proxies: {
    list: () => 
      request<ProxyDetail[]>('/proxies'),
    get: (proxyId: string) => 
      request<ProxyDetail>(`/proxies/${proxyId}`),
    getByDongle: (dongleId: string) => 
      request<ProxyDetail>(`/dongles/${dongleId}/proxy`),
    create: (dongleId: string, data: { enabled?: boolean } = {}) => 
      request<ProxyWithPassword>(`/dongles/${dongleId}/proxy`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    start: (proxyId: string) => 
      request<void>(`/proxies/${proxyId}/start`, { method: 'POST' }),
    stop: (proxyId: string) => 
      request<void>(`/proxies/${proxyId}/stop`, { method: 'POST' }),
    restart: (proxyId: string) => 
      request<void>(`/proxies/${proxyId}/restart`, { method: 'POST' }),
    rotate: (proxyId: string) => 
      request<ProxyWithPassword>(`/proxies/${proxyId}/rotate`, { method: 'POST' }),
    delete: (proxyId: string) => 
      request<void>(`/proxies/${proxyId}`, { method: 'DELETE' }),
  },
  allowedHosts: {
    list: () =>
      request<AllowedHost[]>('/allowed-hosts'),
    add: (host: string) =>
      request<AllowedHost>('/allowed-hosts', {
        method: 'POST',
        body: JSON.stringify({ host }),
      }),
    remove: (host: string) =>
      request<void>(`/allowed-hosts?host=${encodeURIComponent(host)}`, { method: 'DELETE' }),
    listForProxy: (proxyId: string) =>
      request<AllowedHost[]>(`/proxies/${proxyId}/allowed-hosts`),
    addForProxy: (proxyId: string, host: string) =>
      request<AllowedHost>(`/proxies/${proxyId}/allowed-hosts`, {
        method: 'POST',
        body: JSON.stringify({ host }),
      }),
    removeForProxy: (proxyId: string, host: string) =>
      request<void>(`/proxies/${proxyId}/allowed-hosts?host=${encodeURIComponent(host)}`, { method: 'DELETE' }),
  },
  localConnections: {
    list: () => 
      request<LocalConnection[]>('/local-connections'),
    get: (interfaceName: string) => 
      request<LocalConnectionView>(`/local-connections/${encodeURIComponent(interfaceName)}`),
    create: (interfaceName: string, enabled?: boolean) => 
      request<ProxyWithPassword>('/local-connections', {
        method: 'POST',
        body: JSON.stringify({ interface_name: interfaceName, enabled }),
      }),
    delete: (interfaceName: string) => 
      request<void>(`/local-connections/${interfaceName}`, { method: 'DELETE' }),
  },
  settings: {
    getListenInterface: () =>
      request<ProxyListenInterfaceSetting>('/settings/proxy-listen-interface'),
    setListenInterface: (interfaceName: string | null) =>
      request<ProxyListenInterfaceSetting>('/settings/proxy-listen-interface', {
        method: 'PUT',
        body: JSON.stringify({ interface_name: interfaceName }),
      }),
  },
};
