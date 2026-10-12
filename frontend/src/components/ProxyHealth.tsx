import React from 'react';
import { MetricRow } from './MetricRow';

export type ProxyHealthState = 'healthy' | 'unhealthy' | 'unknown';

const HEALTH_TONE: Record<ProxyHealthState, string> = {
  healthy: 'var(--state-online)',
  unhealthy: 'var(--state-error)',
  unknown: 'var(--border-color)',
};

export const ProxyHealth: React.FC<{ health: ProxyHealthState | null; reason: string | null }> = ({
  health,
  reason,
}) => (
  <>
    <MetricRow
      label="Proxy Health"
      value={
        health ? (
          <span
            className="badge"
            style={{ backgroundColor: HEALTH_TONE[health], color: 'white' }}
          >
            {health}
          </span>
        ) : (
          <span className="mono">—</span>
        )
      }
    />
    {health === 'unhealthy' && (
      <div className="notice notice-warning" role="alert">
        Proxy unreachable{reason ? ` — ${reason}` : ''}. Check the proxy container state
        and that its listen port is published and reachable.
      </div>
    )}
  </>
);
