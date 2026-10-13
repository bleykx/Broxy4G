import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../../lib/api';
import { errorMessage } from '../../lib/errors';
import { LocalConnection, ProxyDetail } from '../../types/api';
import { LocalConnectionRow } from '../../components/LocalConnectionRow';
import { ConfirmDialog } from '../../components/ConfirmDialog';

// Enrolled local uplinks on the dashboard: list + unenroll only.
export const LocalUplinksSection: React.FC = () => {
  const [connections, setConnections] = useState<LocalConnection[]>([]);
  const [proxies, setProxies] = useState<ProxyDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unenrollTarget, setUnenrollTarget] = useState<LocalConnection | null>(null);
  const [unenrolling, setUnenrolling] = useState(false);
  const [unenrollError, setUnenrollError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [conns, proxs] = await Promise.allSettled([
      api.localConnections.list(),
      api.proxies.list(),
    ]);
    if (conns.status === 'fulfilled') {
      setConnections(conns.value);
      setError(null);
    } else {
      setError('Failed to load local uplinks.');
    }
    setProxies(proxs.status === 'fulfilled' ? proxs.value : []);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load]);

  const confirmUnenroll = async () => {
    if (!unenrollTarget) return;
    setUnenrolling(true);
    setUnenrollError(null);
    try {
      await api.localConnections.delete(unenrollTarget.interface_name);
      setUnenrollTarget(null);
      await load();
    } catch (e) {
      setUnenrollError(errorMessage(e));
    } finally {
      setUnenrolling(false);
    }
  };

  const enrolled = connections.filter(c => c.enrolled);
  const proxyById = new Map(proxies.map(p => [p.id, p]));

  if (error) {
    return (
      <section className="local-uplinks">
        <h2 className="local-uplinks-title">Local uplinks</h2>
        <div className="notice notice-error">{error}</div>
      </section>
    );
  }

  if (loading || enrolled.length === 0) {
    return null;
  }

  return (
    <section className="local-uplinks">
      <h2 className="local-uplinks-title">Local uplinks</h2>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        {enrolled.map(c => (
          <LocalConnectionRow
            key={c.interface_name}
            connection={c}
            busy={false}
            disabled={unenrolling}
            proxy={c.proxy_id ? proxyById.get(c.proxy_id) ?? null : null}
            onUnenroll={() => { setUnenrollError(null); setUnenrollTarget(c); }}
          />
        ))}
      </div>

      <ConfirmDialog
        open={unenrollTarget !== null}
        title="Unenroll interface"
        message={unenrollTarget
          ? `Unenroll “${unenrollTarget.interface_name}”? This removes its proxy and routing rules. This cannot be undone.`
          : ''}
        confirmLabel="Unenroll"
        busyLabel="Unenrolling…"
        destructive
        busy={unenrolling}
        error={unenrollError}
        onConfirm={confirmUnenroll}
        onCancel={() => { if (!unenrolling) { setUnenrollTarget(null); setUnenrollError(null); } }}
      />
    </section>
  );
};
