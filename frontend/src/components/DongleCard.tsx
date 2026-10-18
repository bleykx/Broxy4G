import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { DongleSummary } from '../types/api';
import { StateBadge } from './StateBadge';
import { TransportBadge } from './TransportBadge';
import { isPhoneTransport } from '../lib/transport';
import { DongleActionsMenu } from './DongleActionsMenu';
import { CardRotateIpButton, CardResetNotice } from './CardRotateIpButton';

interface DongleCardProps {
  dongle: DongleSummary;
  busy: boolean;
  batchBusy: boolean;
  selected: boolean;
  onSelect: () => void;
  onViewDetails: () => void;
  onReset: () => void;
  onDelete: () => void;
  onRotateNotice: (notice: CardResetNotice) => void;
  onRotateSettled: () => void;
}

const signalTone = (quality: number | null): string => {
  if (quality == null) return 'var(--state-offline)';
  if (quality >= 60) return 'var(--state-online)';
  if (quality >= 30) return 'var(--state-degraded)';
  return 'var(--state-error)';
};

export const DongleCard: React.FC<DongleCardProps> = ({
  dongle,
  busy,
  batchBusy,
  selected,
  onSelect,
  onViewDetails,
  onReset,
  onDelete,
  onRotateNotice,
  onRotateSettled,
}) => {
  const { id, label, state, transport, interface_name, public_ip, signal_quality } = dongle;
  const phone = isPhoneTransport(transport);
  const [rotating, setRotating] = useState(false);
  const cardBusy = busy || rotating;

  return (
    <article className="card dongle-card">
      <div className="dongle-card-header">
        <div className="dongle-card-identity">
          <label className="card-select">
            <input
              type="checkbox"
              checked={selected}
              disabled={batchBusy}
              onChange={onSelect}
              aria-label={`Select ${label} for batch reset`}
            />
          </label>
          <div className="dongle-card-heading">
            <Link to={`/dongles/${id}`} className="dongle-card-title">{label}</Link>
            <TransportBadge transport={transport} />
            <StateBadge state={state} />
          </div>
        </div>
        <DongleActionsMenu
          disabled={cardBusy || batchBusy}
          onViewDetails={onViewDetails}
          onReset={onReset}
          onDelete={onDelete}
        />
      </div>

      <div className="dongle-card-body">
        <div className="metric-row">
          <span className="metric-label">Interface</span>
          <span className="mono">{interface_name || '—'}</span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Public IP</span>
          <span className="mono">{public_ip || '—'}</span>
        </div>
        <div className="rotate-ip-row">
          <CardRotateIpButton
            dongle={dongle}
            externalBusy={busy || batchBusy}
            onBusyChange={setRotating}
            onNotice={onRotateNotice}
            onSettled={onRotateSettled}
          />
        </div>
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
                <div
                  className="signal-bar-fill"
                  style={{ width: `${signal_quality ?? 0}%`, background: signalTone(signal_quality) }}
                />
              </div>
              <span className="mono">{signal_quality != null ? `${signal_quality}%` : '—'}</span>
            </div>
          )}
        </div>
      </div>

      {cardBusy && <div className="card-status">Resetting…</div>}
    </article>
  );
};
