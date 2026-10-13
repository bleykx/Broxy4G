import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { errorMessage } from '../lib/errors';
import { LocalConnectionView } from '../types/api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ProxyManagementCard } from '../components/ProxyManagementCard';
import { ProxyAllowedHostsCard } from '../features/proxies/ProxyAllowedHostsCard';
import { LocalUplinkInfoCard } from '../features/uplinks/LocalUplinkInfoCard';
import { LocalUplinkProxyCard } from '../features/uplinks/LocalUplinkProxyCard';

export const LocalUplinkDetailPage: React.FC = () => {
  const { interface_name: interfaceName } = useParams<{ interface_name: string }>();
  const navigate = useNavigate();
  const [view, setView] = useState<LocalConnectionView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [unenrollOpen, setUnenrollOpen] = useState(false);
  const [unenrolling, setUnenrolling] = useState(false);
  const [unenrollError, setUnenrollError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!interfaceName) return;
    try {
      setView(await api.localConnections.get(interfaceName));
      setLoadError(null);
    } catch (e) {
      setLoadError(errorMessage(e));
    }
    setLoading(false);
  }, [interfaceName]);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 15000);
    return () => clearInterval(interval);
  }, [loadData]);

  const confirmUnenroll = async () => {
    if (!interfaceName) return;
    setUnenrolling(true);
    setUnenrollError(null);
    try {
      await api.localConnections.delete(interfaceName);
      navigate('/');
    } catch (e) {
      setUnenrollError(errorMessage(e));
    } finally {
      setUnenrolling(false);
    }
  };

  if (!interfaceName) return null;

  if (loading) {
    return (
      <div className="main-content">
        <div className="card skeleton-card" />
        <div className="grid-layout">
          {[0, 1, 2].map(i => <div key={i} className="card skeleton-card" />)}
        </div>
      </div>
    );
  }

  if (!view) {
    return (
      <div className="main-content">
        <header className="page-header">
          <div>
            <h1>Interface not found</h1>
            <p className="page-subtitle">It may have been unplugged or renamed.</p>
          </div>
          <button className="btn" onClick={() => navigate('/')}>← Back</button>
        </header>
        {loadError && <div className="notice notice-error">{loadError}</div>}
      </div>
    );
  }

  const subtitle = view.ip ? `${view.kind} · ${view.ip}` : `${view.kind} · ${view.link_state}`;

  return (
    <div className="main-content">
      <header className="page-header">
        <div>
          <h1 className="mono">{view.interface_name}</h1>
          <p className="page-subtitle">{subtitle}</p>
        </div>
        <div className="detail-actions">
          <button className="btn" onClick={() => navigate('/')}>← Back to dashboard</button>
          {view.enrolled && (
            <button
              className="btn btn-danger-text"
              onClick={() => { setUnenrollError(null); setUnenrollOpen(true); }}
            >
              Unenroll
            </button>
          )}
        </div>
      </header>

      {loadError && <div className="notice notice-error">{loadError}</div>}

      <div className="grid-layout">
        <LocalUplinkInfoCard view={view} />
        <LocalUplinkProxyCard view={view} />
        {view.proxy && (
          <ProxyManagementCard
            proxy={view.proxy}
            onChanged={loadData}
            emptyMessage="No proxy configured. The proxy is created when the interface is enrolled."
          />
        )}
        {view.proxy && (
          <ProxyAllowedHostsCard
            proxyId={view.proxy.id}
            containerName={view.proxy.container_name}
          />
        )}
      </div>

      <ConfirmDialog
        open={unenrollOpen}
        title="Unenroll interface"
        message={`Unenroll “${view.interface_name}”? This removes its proxy and routing rules. This cannot be undone.`}
        confirmLabel="Unenroll"
        busyLabel="Unenrolling…"
        destructive
        busy={unenrolling}
        error={unenrollError}
        onConfirm={confirmUnenroll}
        onCancel={() => { if (!unenrolling) { setUnenrollOpen(false); setUnenrollError(null); } }}
      />
    </div>
  );
};
