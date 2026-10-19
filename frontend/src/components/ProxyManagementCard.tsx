import React, { useState } from 'react';
import { api } from '../lib/api';
import { errorMessage } from '../lib/errors';
import { ProxyDetail, ProxyWithPassword } from '../types/api';
import { StateBadge } from './StateBadge';
import { ConfirmDialog } from './ConfirmDialog';
import { CredentialsDialog } from './CredentialsDialog';
import { MetricRow } from './MetricRow';
import { ProxyActions } from './ProxyActions';
import { PROXY_CONFIRM, ProxyConfirmKind } from './proxyConfirmation';

interface ProxyManagementCardProps {
  // Only required when the card may offer proxy creation (dongle pages).
  dongleId?: string;
  proxy: ProxyDetail | null;
  onChanged: () => void;
  actionsDisabled?: boolean;
  emptyMessage?: string;
}

const VIEW_CREDENTIALS_MESSAGE = 'These are the current credentials of this proxy.';

export const ProxyManagementCard: React.FC<ProxyManagementCardProps> = ({
  dongleId,
  proxy,
  onChanged,
  actionsDisabled = false,
  emptyMessage,
}) => {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ProxyConfirmKind | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<{ username: string; password: string; message?: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const applyCredentials = (result: unknown, message?: string) => {
    const pw = result as ProxyWithPassword | undefined;
    if (pw && pw.password != null) {
      setCredentials({ username: pw.proxy.auth_user ?? '', password: pw.password, message });
    }
  };

  const execute = async (action: string, fn: () => Promise<unknown>) => {
    setBusy(action);
    setError(null);
    setNotice(null);
    try {
      const result = await fn();
      if (action === 'create') {
        const pw = result as ProxyWithPassword | undefined;
        if (pw?.password == null) {
          setNotice('This dongle already has a proxy — credentials were left unchanged.');
        }
      }
      applyCredentials(result);
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const viewCredentials = async (proxyId: string) => {
    setBusy('view-credentials');
    setError(null);
    setNotice(null);
    try {
      const { proxy: p, password } = await api.proxies.getCredentials(proxyId);
      if (password == null) {
        setError('Credentials could not be retrieved.');
        return;
      }
      setCredentials({ username: p.auth_user ?? '', password, message: VIEW_CREDENTIALS_MESSAGE });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const openConfirm = (kind: ProxyConfirmKind) => {
    setConfirmError(null);
    setConfirm(kind);
  };

  const handleConfirm = async () => {
    if (!proxy || !confirm) return;
    setConfirmBusy(true);
    setConfirmError(null);
    try {
      if (confirm === 'delete') {
        await api.proxies.delete(proxy.id);
      } else {
        applyCredentials(await api.proxies.rotate(proxy.id));
      }
      setConfirm(null);
      onChanged();
    } catch (e) {
      setConfirmError(errorMessage(e));
    } finally {
      setConfirmBusy(false);
    }
  };

  const disabled = busy !== null || confirmBusy || actionsDisabled;
  const dialog = confirm ? PROXY_CONFIRM[confirm] : null;

  return (
    <div className="card">
      <h3 className="card-title">Proxy Management</h3>
      {error && <p className="card-error">{error}</p>}
      {notice && <div className="notice notice-warning card-notice">{notice}</div>}
      {proxy ? (
        <>
          <MetricRow label="Container" value={<span className="mono">{proxy.container_name}</span>} />
          <MetricRow label="Port" value={<span className="mono">{proxy.listen_port}</span>} />
          <MetricRow label="State" value={<StateBadge state={proxy.state} />} />
          <MetricRow label="Enabled" value={proxy.enabled ? 'Yes' : 'No'} />
          <MetricRow label="Network IP" value={<span className="mono">{proxy.network_ip ?? '—'}</span>} />
          <MetricRow
            label="Auth User"
            value={
              <span
                className="mono"
                title="Proxy authentication username — the password is available via the “View credentials” button."
              >
                {proxy.auth_user ?? '—'}
              </span>
            }
          />
          <ProxyActions
            busy={busy}
            disabled={disabled}
            onStart={() => execute('start', () => api.proxies.start(proxy.id))}
            onStop={() => execute('stop', () => api.proxies.stop(proxy.id))}
            onRestart={() => execute('restart', () => api.proxies.restart(proxy.id))}
            onViewCredentials={() => viewCredentials(proxy.id)}
            onRotate={() => openConfirm('rotate')}
            onDelete={() => openConfirm('delete')}
          />
        </>
      ) : (
        <div className="proxy-empty">
          <p>{emptyMessage ?? 'No proxy configured for this dongle.'}</p>
          {dongleId && (
            <>
              <p className="proxy-hint">The listen port is allocated automatically.</p>
              <button className="btn btn-primary" disabled={disabled} onClick={() => execute('create', () => api.proxies.create(dongleId, {}))}>
                {busy === 'create' ? 'Creating…' : 'Create Proxy'}
              </button>
            </>
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirm !== null}
        title={dialog?.title ?? ''}
        message={dialog?.message ?? ''}
        confirmLabel={dialog?.confirmLabel ?? ''}
        busyLabel={dialog?.busyLabel ?? ''}
        destructive={dialog?.destructive}
        busy={confirmBusy}
        error={confirmError}
        onConfirm={handleConfirm}
        onCancel={() => { if (!confirmBusy) { setConfirm(null); setConfirmError(null); } }}
      />
      <CredentialsDialog
        open={credentials !== null}
        username={credentials?.username ?? ''}
        password={credentials?.password ?? ''}
        message={credentials?.message}
        onClose={() => setCredentials(null)}
      />
    </div>
  );
};
