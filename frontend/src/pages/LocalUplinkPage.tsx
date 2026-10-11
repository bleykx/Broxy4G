import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { errorMessage } from '../lib/errors';
import { LocalConnection } from '../types/api';
import { LocalConnectionRow } from '../components/LocalConnectionRow';
import { CredentialsDialog } from '../components/CredentialsDialog';

type Notice = { kind: 'success' | 'warning' | 'error'; text: string } | null;

export const LocalUplinkPage: React.FC = () => {
  const navigate = useNavigate();
  const [connections, setConnections] = useState<LocalConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<{ username: string; password: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.localConnections.list();
      setConnections(data);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleEnroll = async (conn: LocalConnection) => {
    setBusyId(conn.interface_name);
    setNotice(null);
    try {
      const result = await api.localConnections.create(conn.interface_name);
      if (result.password != null) {
        setCredentials({ username: result.proxy.auth_user ?? '', password: result.password });
      }
      setNotice({ kind: 'success', text: `Enrolled ${conn.interface_name}.` });
      await load();
    } catch (e) {
      setNotice({ kind: 'error', text: errorMessage(e) });
    } finally {
      setBusyId(null);
    }
  };

  const candidates = connections.filter(c => !c.enrolled);

  return (
    <div className="main-content">
      <header className="page-header">
        <div>
          <h1>Available interfaces</h1>
          <p className="page-subtitle">
            Enroll a host network interface as a proxy uplink.
          </p>
        </div>
        <div className="page-header-actions">
          <button className="btn" onClick={() => navigate('/')}>← Back</button>
          <button className="btn" onClick={load} disabled={loading}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </header>

      {notice && <div className={`notice notice-${notice.kind}`}>{notice.text}</div>}
      {error && <div className="notice notice-error">{error}</div>}

      {loading && connections.length === 0 ? (
        <div className="grid-layout">
          {[0, 1].map(i => <div key={i} className="card skeleton-card" />)}
        </div>
      ) : !error && candidates.length === 0 ? (
        <div className="empty-state">
          <h2>No host interfaces available</h2>
          <p>No host interface is available to enroll as a proxy uplink.</p>
          <button className="btn" onClick={load}>Refresh</button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {candidates.map(c => (
            <LocalConnectionRow
              key={c.interface_name}
              connection={c}
              busy={busyId === c.interface_name}
              disabled={busyId !== null}
              onEnroll={() => handleEnroll(c)}
            />
          ))}
        </div>
      )}

      <CredentialsDialog
        open={credentials !== null}
        username={credentials?.username ?? ''}
        password={credentials?.password ?? ''}
        onClose={() => setCredentials(null)}
      />
    </div>
  );
};
