import React, { useState } from 'react';
import { PoolDetail, ProxyDetail } from '../../types/api';
import { api } from '../../lib/api';
import { errorMessage } from '../../lib/errors';
import { useCopyToClipboard, CopyStatus } from '../../lib/useCopyToClipboard';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { CredentialsDialog } from '../../components/CredentialsDialog';
import { PoolMembers } from './PoolMembers';

const copyLabel = (s: CopyStatus, base: string) => (s === 'copied' ? 'Copié' : s === 'failed' ? 'Échec' : base);

interface PoolCardProps {
  pool: PoolDetail;
  proxies: ProxyDetail[];
  busy: boolean;
  onSettled: () => void;
}

export const PoolCard: React.FC<PoolCardProps> = ({ pool, proxies, busy, onSettled }) => {
  const [error, setError] = useState<string | null>(null);
  const [credsOpen, setCredsOpen] = useState(false);
  const [creds, setCreds] = useState<{ username: string; password: string } | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const { status: epStatus, copy: copyEp, reset: resetEp } = useCopyToClipboard();

  const disabled = busy || acting;

  const run = async (action: () => Promise<unknown>) => {
    setError(null);
    setActing(true);
    try {
      await action();
      onSettled();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setActing(false);
    }
  };

  const showCredentials = async () => {
    setError(null);
    try {
      const res = await api.pools.getCredentials(pool.id);
      if (res.password) { setCreds({ username: res.pool.auth_user ?? '', password: res.password }); setCredsOpen(true); }
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const confirmDelete = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.pools.delete(pool.id);
      setDeleteOpen(false);
      onSettled();
    } catch (e) {
      setDeleteError(errorMessage(e));
    } finally {
      setDeleting(false);
    }
  };

  const listeningBadge = pool.enabled
    ? pool.listening
      ? { text: 'Écoute actif', color: 'var(--state-online)' }
      : { text: pool.listen_error ?? 'Écoute indisponible', color: 'var(--state-error)' }
    : { text: 'Désactivé', color: 'var(--state-offline)' };

  return (
    <div className="card pool-card">
      <div className="pool-card-header">
        <h3 className="pool-card-title">{pool.label}</h3>
        <div className="pool-card-badges">
          <span className="badge" style={{ backgroundColor: listeningBadge.color, color: 'white' }}>
            {listeningBadge.text}
          </span>
          <span className="badge" style={{ backgroundColor: 'var(--border-color)', color: 'white' }}>
            :{pool.listen_port}
          </span>
        </div>
      </div>

      {error && <p className="card-error">{error}</p>}

      <div className="metric-row">
        <span className="metric-label">Endpoint</span>
        {pool.endpoint ? (
          <div className="endpoint-copy" onMouseLeave={resetEp}>
            <span className="mono endpoint-url">{pool.endpoint}</span>
            <button className="btn btn-sm" onClick={() => copyEp(pool.endpoint!)}>
              {copyLabel(epStatus, 'Copier')}
            </button>
          </div>
        ) : (
          <span className="pool-muted">Adresse publique non résolue pour l'instant</span>
        )}
      </div>

      <div className="metric-row">
        <span className="metric-label">Utilisateur</span>
        <span className="mono">{pool.auth_user ?? '—'}</span>
      </div>

      <PoolMembers pool={pool} proxies={proxies} busy={disabled} onSettled={onSettled} />

      <div className="pool-card-footer">
        <button className="btn btn-sm" disabled={disabled} onClick={showCredentials}>
          Afficher le mot de passe
        </button>
        <button className="btn btn-sm" disabled={disabled}
          onClick={() => run(() => api.pools.update(pool.id, { enabled: !pool.enabled }))}>
          {pool.enabled ? 'Désactiver' : 'Activer'}
        </button>
        <button className="btn btn-sm btn-danger-text" disabled={disabled}
          onClick={() => { setDeleteError(null); setDeleteOpen(true); }}>
          Supprimer
        </button>
      </div>

      <CredentialsDialog
        open={credsOpen}
        username={creds?.username ?? ''}
        password={creds?.password ?? ''}
        message="Mot de passe du pool, conservé chiffré et consultable depuis cette page. Copiez-le pour configurer vos clients."
        onClose={() => { setCredsOpen(false); setCreds(null); }}
      />

      <ConfirmDialog
        open={deleteOpen}
        title="Supprimer le pool"
        message={`Supprimer « ${pool.label} » ? L'écoute est arrêtée et les membres détachés. Cette action est définitive.`}
        confirmLabel="Supprimer"
        busyLabel="Suppression…"
        destructive
        busy={deleting}
        error={deleteError}
        onConfirm={confirmDelete}
        onCancel={() => { if (!deleting) { setDeleteOpen(false); setDeleteError(null); } }}
      />
    </div>
  );
};
