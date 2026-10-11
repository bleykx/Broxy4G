import React from 'react';
import { Link } from 'react-router-dom';
import { DongleSummary } from '../types/api';
import { StateBadge } from './StateBadge';
import { DongleActionsMenu } from './DongleActionsMenu';

interface DongleCardProps {
  dongle: DongleSummary;
  busy: boolean;
  batchBusy: boolean;
  selected: boolean;
  onSelect: () => void;
  onViewDetails: () => void;
  onReset: () => void;
  onDelete: () => void;
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
}) => {
  const { id, label, state, interface_name, public_ip, signal_quality } = dongle;

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
            <StateBadge state={state} />
          </div>
        </div>
        <DongleActionsMenu
          disabled={busy || batchBusy}
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
        <div className="metric-row">
          <span className="metric-label">Signal</span>
          <div className="signal-container">
            <div className="signal-bar-bg">
              <div
                className="signal-bar-fill"
                style={{ width: `${signal_quality ?? 0}%`, background: signalTone(signal_quality) }}
              />
            </div>
            <span className="mono">{signal_quality != null ? `${signal_quality}%` : '—'}</span>
          </div>
        </div>
      </div>

      {busy && <div className="card-status">Resetting…</div>}
    </article>
  );
};
