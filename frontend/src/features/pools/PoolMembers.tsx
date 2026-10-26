import React, { useState } from 'react';
import { PoolDetail, ProxyDetail } from '../../types/api';
import { api } from '../../lib/api';
import { errorMessage } from '../../lib/errors';

const shortId = (id: string) => id.slice(0, 8);

interface PoolMembersProps {
  pool: PoolDetail;
  proxies: ProxyDetail[];
  busy: boolean;
  onSettled: () => void;
}

export const PoolMembers: React.FC<PoolMembersProps> = ({ pool, proxies, busy, onSettled }) => {
  const [selected, setSelected] = useState('');
  const [error, setError] = useState<string | null>(null);

  const memberIds = new Set(pool.members.map(m => m.proxy_id));
  const candidates = proxies.filter(p => !memberIds.has(p.id));

  const addMember = async () => {
    if (!selected) return;
    setError(null);
    try {
      await api.pools.addMember(pool.id, selected);
      setSelected('');
      onSettled();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const removeMember = async (proxyId: string) => {
    setError(null);
    try {
      await api.pools.removeMember(pool.id, proxyId);
      onSettled();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <div className="pool-members-block">
      <div className="metric-row">
        <span className="metric-label">Membres éligibles</span>
        <span className="mono">{pool.eligible_count} / {pool.members.length}</span>
      </div>

      {error && <p className="card-error">{error}</p>}

      {pool.members.length > 0 ? (
        <ul className="pool-members">
          {pool.members.map(m => (
            <li key={m.proxy_id} className={`pool-member ${m.eligible ? '' : 'pool-member-ineligible'}`}>
              <span className="mono" title={m.proxy_id}>{m.label ?? shortId(m.proxy_id)}</span>
              <span className="pool-member-detail">
                {m.eligible ? (m.target ?? 'éligible') : (m.reason ?? 'non éligible')}
              </span>
              <button
                className="btn btn-sm btn-danger-text"
                disabled={busy}
                onClick={() => removeMember(m.proxy_id)}
              >
                Retirer
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="pool-hint">Aucun membre pour l'instant.</p>
      )}

      <div className="pool-add-member">
        <select
          className="input"
          value={selected}
          disabled={busy}
          onChange={e => setSelected(e.target.value)}
        >
          <option value="">
            {candidates.length === 0 ? 'Tous les proxies sont déjà membres' : 'Choisir un proxy…'}
          </option>
          {candidates.map(p => (
            <option key={p.id} value={p.id}>
              {p.container_name} :{p.listen_port} ({p.state})
            </option>
          ))}
        </select>
        <button className="btn btn-sm" disabled={busy || !selected} onClick={addMember}>
          Ajouter
        </button>
      </div>
    </div>
  );
};
