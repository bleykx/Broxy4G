import React from 'react';
import { LocalConnectionView } from '../../types/api';
import { MetricRow } from '../../components/MetricRow';
import { StateBadge } from '../../components/StateBadge';
import { ProxyHealth } from '../../components/ProxyHealth';

export const LocalUplinkProxyCard: React.FC<{ view: LocalConnectionView }> = ({ view }) => {
  const proxy = view.proxy;

  return (
    <div className="card">
      <h3 className="card-title">Proxy / Metrics</h3>
      {proxy ? (
        <>
          <MetricRow label="State" value={<StateBadge state={proxy.state} />} />
          <MetricRow label="Container" value={<span className="mono">{proxy.container_name}</span>} />
          <MetricRow label="Listen Port" value={<span className="mono">{proxy.listen_port}</span>} />
          <MetricRow label="Auth User" value={<span className="mono">{proxy.auth_user ?? '—'}</span>} />
          <MetricRow label="Network IP" value={<span className="mono">{proxy.network_ip ?? '—'}</span>} />
          <MetricRow label="Enabled" value={proxy.enabled ? 'Yes' : 'No'} />
        </>
      ) : (
        <p className="proxy-hint">No proxy is configured for this uplink.</p>
      )}
      <ProxyHealth health={view.proxy_health} reason={view.proxy_health_reason} />
    </div>
  );
};
