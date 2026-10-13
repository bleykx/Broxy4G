import React from 'react';
import { LocalConnectionView } from '../../types/api';
import { MetricRow } from '../../components/MetricRow';
import { LinkStateBadge } from './LinkStateBadge';

export const LocalUplinkInfoCard: React.FC<{ view: LocalConnectionView }> = ({ view }) => (
  <div className="card">
    <h3 className="card-title">General Info</h3>
    <MetricRow label="Interface" value={<span className="mono">{view.interface_name}</span>} />
    <MetricRow label="Kind" value={view.kind} />
    <MetricRow label="Link State" value={<LinkStateBadge state={view.link_state} />} />
    <MetricRow label="Local IP" value={<span className="mono">{view.ip ?? '—'}</span>} />
    <MetricRow label="Gateway" value={<span className="mono">{view.gateway ?? '—'}</span>} />
    <MetricRow
      label="DNS"
      value={
        <span className="mono">
          {view.dns && view.dns.length > 0 ? view.dns.join(', ') : '—'}
        </span>
      }
    />
    <MetricRow label="Enrolled" value={view.enrolled ? 'Yes' : 'No'} />
  </div>
);
