import React, { useState, useEffect, useCallback } from 'react';
import { PoolDetail, ProxyDetail } from '../../types/api';
import { api } from '../../lib/api';
import { errorMessage } from '../../lib/errors';
import { useCopyToClipboard, CopyStatus } from '../../lib/useCopyToClipboard';
import { PoolCard } from './PoolCard';

const copyLabel = (status: CopyStatus, base: string) =>
  status === 'copied' ? 'Copié' : status === 'failed' ? 'Échec' : base;

export const PoolsPage: React.FC = () => {
  const [pools, setPools] = useState<PoolDetail[]>([]);
  const [proxies, setProxies] = useState<ProxyDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [newPassword, setNewPassword] = useState<string | null>(null);
  const { status: pwStatus, copy: copyPw, reset: resetPw } = useCopyToClipboard();

  const load = useCallback(async () => {
    try {
      const [poolRes, proxyRes] = await Promise.allSettled([api.pools.list(), api.proxies.list()]);
      if (proxyRes.status === 'fulfilled') setProxies(proxyRes.value);
      if (poolRes.status === 'fulfilled') {
        setPools(poolRes.value);
        setError(null);
      } else {
        setError(errorMessage(poolRes.reason));
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load]);

  const createPool = async () => {
    const trimmed = label.trim();
    if (!trimmed) return;
    setCreating(true);
    setCreateError(null);
    setNewPassword(null);
    try {
      const res = await api.pools.create({ label: trimmed });
      setLabel('');
      if (res.password) setNewPassword(res.password);
      await load();
    } catch (e) {
      setCreateError(errorMessage(e));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="main-content">
      <header className="page-header">
        <div>
          <h1>Pools de proxies</h1>
          <p className="page-subtitle">
            {pools.length === 1 ? '1 pool' : `${pools.length} pools`} — un port unique devant plusieurs proxies
          </p>
        </div>
      </header>

      <div className="card pool-create-card">
        <div className="card-title">Créer un pool</div>
        <div className="pool-create-form">
          <input
            className="input"
            placeholder="Nom du pool"
            value={label}
            onChange={e => setLabel(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') createPool(); }}
          />
          <button className="btn btn-primary" disabled={creating || label.trim() === ''} onClick={createPool}>
            {creating ? 'Création…' : 'Créer'}
          </button>
        </div>
        {createError && <p className="card-error">{createError}</p>}
      </div>

      {newPassword && (
        <div className="pool-password-banner" onMouseLeave={resetPw}>
          <div>
            <strong>Mot de passe du pool</strong> — copiez-le maintenant, il n'est plus affiché ici ensuite.
          </div>
          <div className="pool-password-row">
            <span className="mono">{newPassword}</span>
            <button className="btn btn-sm btn-primary" onClick={() => copyPw(newPassword)}>
              {copyLabel(pwStatus, 'Copier')}
            </button>
            <button className="btn btn-sm" onClick={() => setNewPassword(null)}>Fermer</button>
          </div>
        </div>
      )}

      {error && (
        <div className="notice notice-error">
          <div>{error}</div>
          <button className="btn btn-sm" onClick={load}>Réessayer</button>
        </div>
      )}

      {loading && pools.length === 0 ? (
        <div className="grid-layout">
          {[0, 1].map(i => <div key={i} className="card pool-card skeleton-card" />)}
        </div>
      ) : pools.length === 0 ? (
        <div className="empty-state">
          <h2>Aucun pool</h2>
          <p>
            Un pool regroupe plusieurs proxies derrière un port unique : un client qui contacte ce
            port est servi par l'un des membres éligibles. Créez votre premier pool ci-dessus.
          </p>
        </div>
      ) : (
        <div className="grid-layout">
          {pools.map(p => (
            <PoolCard
              key={p.id}
              pool={p}
              proxies={proxies}
              busy={creating}
              onSettled={load}
            />
          ))}
        </div>
      )}
    </div>
  );
};
