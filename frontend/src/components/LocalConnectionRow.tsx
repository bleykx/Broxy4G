import React from 'react';
import { Link } from 'react-router-dom';
import { LocalConnection, LocalInterfaceKind, ProxyDetail } from '../types/api';
import { StateBadge } from './StateBadge';
import { LinkStateBadge } from '../features/uplinks/LinkStateBadge';

interface LocalConnectionRowProps {
  connection: LocalConnection;
  busy: boolean;
  disabled: boolean;
  proxy?: ProxyDetail | null;
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
  proxy = null,
  onEnroll,
  onUnenroll,
}) => {
  const { interface_name, ip, kind, enrolled, gateway, link_state } = connection;
  const detailsTo = `/local/${encodeURIComponent(interface_name)}`;

  return (
    <article className="card dongle-card">
      <div className="dongle-card-header">
        <div className="dongle-card-heading">
          <Link to={detailsTo} className="dongle-card-title mono">{interface_name}</Link>
          <span
            className="badge"
            style={{ backgroundColor: KIND_COLORS[kind], color: 'white' }}
          >
            {kind}
          </span>
          <LinkStateBadge state={link_state} />
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
        <div className="metric-row">
          <span className="metric-label">Gateway</span>
          <span className="mono">{gateway ?? '—'}</span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Proxy</span>
          {proxy ? (
            <span className="proxy-row-value">
              <StateBadge state={proxy.state} />
              <span className="mono">:{proxy.listen_port}</span>
            </span>
          ) : (
            <span className="mono">—</span>
          )}
        </div>
      </div>

      <div className="dongle-card-footer">
        <Link to={detailsTo} className="details-link">
          Details →
        </Link>
      </div>

      {busy && <div className="card-status">Enrolling…</div>}
    </article>
  );
};
