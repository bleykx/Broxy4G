import React from 'react';
import { DongleDetail } from '../../types/api';
import { MetricRow } from '../../components/MetricRow';
import { ProxyHealth } from '../../components/ProxyHealth';
import { isPhoneTransport } from '../../lib/transport';

const signalTone = (quality: number | null): string => {
  if (quality == null) return 'var(--state-offline)';
  if (quality >= 60) return 'var(--state-online)';
  if (quality >= 30) return 'var(--state-degraded)';
  return 'var(--state-error)';
};

export const DongleMetricsCard: React.FC<{ dongle: DongleDetail }> = ({ dongle }) => {
  const phone = isPhoneTransport(dongle.transport);
  const signal = dongle.metrics?.signal_quality ?? null;
  const percent = signal?.percent ?? null;

  return (
    <div className="card">
      <h3 className="card-title">Metrics</h3>
      <MetricRow label="Public IP" value={<span className="mono">{dongle.metrics?.public_ip || '—'}</span>} />
      <div className="metric-row">
        <span className="metric-label">Signal</span>
        {phone ? (
          <div className="signal-container">
            <div className="signal-bar-bg" />
            <span className="mono">—</span>
            <span className="signal-na" title="Signal quality is not available on a phone">n/a</span>
          </div>
        ) : (
          <div className="signal-container">
            <div className="signal-bar-bg">
              <div className="signal-bar-fill" style={{ width: `${percent ?? 0}%`, background: signalTone(percent) }} />
            </div>
            <span className="mono">{percent != null ? `${percent}%` : '—'}</span>
          </div>
        )}
      </div>
      {signal?.rsrp != null && <MetricRow label="RSRP" value={<span className="mono">{signal.rsrp} dBm</span>} />}
      {signal?.rsrq != null && <MetricRow label="RSRQ" value={<span className="mono">{signal.rsrq} dB</span>} />}
      {signal?.sinr != null && <MetricRow label="SINR" value={<span className="mono">{signal.sinr} dB</span>} />}
      <MetricRow label="Avg Ping" value={<span className="mono">{dongle.metrics?.ping?.avg_ms ?? '—'} ms</span>} />
      <ProxyHealth
        health={dongle.metrics?.proxy_health ?? null}
        reason={dongle.metrics?.proxy_health_reason ?? null}
      />
    </div>
  );
};
