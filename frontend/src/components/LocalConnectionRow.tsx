import React from 'react';
import { LocalConnection, LocalInterfaceKind } from '../types/api';

interface LocalConnectionRowProps {
  connection: LocalConnection;
  busy: boolean;
  disabled: boolean;
  onEnroll?: () => void;
  onUnenroll?: () => void;
}

const KIND_COLORS: Record<LocalInterfaceKind, string> = {
  ethernet: 'var(--state-connecting)',
  wifi: 'var(--state-configuring)',
  other: 'var(--state-offline)',
};

export const LocalConnectionRow: React.FC<LocalConnectionRowProps> = ({
  connection,
  busy,
  disabled,
  onEnroll,
  onUnenroll,
}) => {
  const { interface_name, ip, kind, enrolled } = connection;

  return (
    <article className="card dongle-card">
      <div className="dongle-card-header">
        <div className="dongle-card-heading">
          <span className="dongle-card-title mono">{interface_name}</span>
          <span
            className="badge"
            style={{ backgroundColor: KIND_COLORS[kind], color: 'white' }}
          >
            {kind}
          </span>
          {enrolled && (
            <span
              className="badge"
              style={{ backgroundColor: 'var(--state-online)', color: 'white' }}
            >
              Enrolled
            </span>
          )}
        </div>
        {enrolled ? (
          onUnenroll && (
            <button
              className="btn btn-danger-text"
              disabled={disabled}
              onClick={onUnenroll}
              aria-label={`Unenroll ${interface_name}`}
            >
              Unenroll
            </button>
          )
        ) : (
          onEnroll && (
            <button
              className="btn btn-primary"
              disabled={disabled}
              onClick={onEnroll}
              aria-label={`Enroll ${interface_name}`}
            >
              {busy ? 'Enrolling…' : 'Enroll'}
            </button>
          )
        )}
      </div>

      <div className="dongle-card-body">
        <div className="metric-row">
          <span className="metric-label">IP</span>
          <span className="mono">{ip ?? '—'}</span>
        </div>
      </div>

      {busy && <div className="card-status">Enrolling…</div>}
    </article>
  );
};
