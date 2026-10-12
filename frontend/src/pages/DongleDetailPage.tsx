import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { errorMessage } from '../lib/errors';
import { DongleDetail, ProxyDetail } from '../types/api';
import { StateBadge } from '../components/StateBadge';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ProxyManagementCard } from '../components/ProxyManagementCard';
import { ProxyAllowedHostsCard } from '../features/proxies/ProxyAllowedHostsCard';
import { ResetIpControl } from '../components/ResetIpControl';
import { MetricRow } from '../components/MetricRow';
import { ProxyHealth } from '../components/ProxyHealth';
import { useCopyToClipboard } from '../lib/useCopyToClipboard';

type Notice = { kind: 'success' | 'warning' | 'error'; text: string } | null;

interface EditFormState { label: string; apn: string; apn_auth: string }

const signalTone = (quality: number | null): string => {
  if (quality == null) return 'var(--state-offline)';
  if (quality >= 60) return 'var(--state-online)';
  if (quality >= 30) return 'var(--state-degraded)';
  return 'var(--state-error)';
};

const ErrorDetailPanel: React.FC<{ detail: string | null }> = ({ detail }) => (
  <div className="error-panel" role="alert">
    <h3 className="error-panel-title">Connection failed</h3>
    {detail ? (
      <pre className="mono error-panel-detail">{detail}</pre>
    ) : (
      <p className="error-panel-detail">No error detail available.</p>
    )}
  </div>
);

const EditConfigForm: React.FC<{
  form: EditFormState;
  saving: boolean;
  onChange: (form: EditFormState) => void;
  onSubmit: (e: React.FormEvent) => void;
}> = ({ form, saving, onChange, onSubmit }) => (
  <form onSubmit={onSubmit} className="card edit-form">
    <h3 className="card-title">Configuration</h3>
    <div className="form-grid">
      <div className="form-group">
        <label htmlFor="edit-label">Label</label>
        <input id="edit-label" className="input" value={form.label}
          onChange={e => onChange({ ...form, label: e.target.value })} />
      </div>
      <div className="form-group">
        <label htmlFor="edit-apn">APN</label>
        <input id="edit-apn" className="input" value={form.apn}
          onChange={e => onChange({ ...form, apn: e.target.value })} />
      </div>
      <div className="form-group">
        <label htmlFor="edit-apn-auth">APN Auth</label>
        <select id="edit-apn-auth" className="input" value={form.apn_auth}
          onChange={e => onChange({ ...form, apn_auth: e.target.value })}>
          <option value="none">none</option>
          <option value="pap">pap</option>
          <option value="chap">chap</option>
        </select>
      </div>
    </div>
    <button className="btn btn-primary" type="submit" disabled={saving}>
      {saving ? 'Saving…' : 'Save Changes'}
    </button>
  </form>
);

export const DongleDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [dongle, setDongle] = useState<DongleDetail | null>(null);
  const [proxy, setProxy] = useState<ProxyDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editForm, setEditForm] = useState<EditFormState>({ label: '', apn: '', apn_auth: 'none' });
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const endpointCopy = useCopyToClipboard();
  const editingRef = useRef(false);

  const loadData = useCallback(async () => {
    if (!id) return;
    try {
      const d = await api.dongles.get(id);
      setDongle(d);
      setLoadError(null);
      if (!editingRef.current) setEditForm({ label: d.label, apn: d.apn, apn_auth: d.apn_auth ?? 'none' });
    } catch (e) {
      setLoadError(errorMessage(e));
    }
    try {
      setProxy(await api.proxies.getByDongle(id));
    } catch {
      setProxy(null);
    }
    setLoading(false);
  }, [id]);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 15000);
    return () => clearInterval(interval);
  }, [loadData]);

  const toggleEditing = () => {
    const next = !editingRef.current;
    editingRef.current = next;
    setEditing(next);
    setNotice(null);
  };

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id) return;
    setSaving(true);
    setNotice(null);
    try {
      await api.dongles.update(id, editForm);
      toggleEditing();
      setNotice({ kind: 'success', text: 'Configuration saved.' });
      loadData();
    } catch (err) {
      setNotice({ kind: 'error', text: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = async () => {
    if (!id) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.dongles.delete(id);
      navigate('/');
    } catch (e) {
      setDeleteError(errorMessage(e));
    } finally {
      setDeleting(false);
    }
  };

  if (!id) return null;

  const resetUrl = `${window.location.origin}/api/v1/dongles/${id}/reset`;

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

  if (!dongle) {
    return (
      <div className="main-content">
        <header className="page-header">
          <div>
            <h1>Dongle not found</h1>
            <p className="page-subtitle">It may have been unenrolled or unplugged.</p>
          </div>
          <button className="btn" onClick={() => navigate('/')}>← Back</button>
        </header>
        {loadError && <div className="notice notice-error">{loadError}</div>}
      </div>
    );
  }

  const signal = dongle.metrics?.signal_quality ?? null;
  const percent = signal?.percent ?? null;

  return (
    <div className="main-content">
      <header className="page-header">
        <div>
          <h1>{dongle.label}</h1>
          <p className="page-subtitle">{dongle.interface_name} · route table {dongle.route_table_id}</p>
        </div>
        <div className="detail-actions">
          <button className="btn" onClick={() => navigate('/')}>← Back</button>
          <button className="btn btn-primary" onClick={toggleEditing}>
            {editing ? 'Cancel' : 'Edit Config'}
          </button>
          <ResetIpControl dongleId={id} dongleLabel={dongle.label} onChanged={loadData} onNotice={setNotice} />
          <button className="btn btn-danger-text" onClick={() => { setDeleteError(null); setDeleteOpen(true); }}>
            Delete Dongle
          </button>
        </div>
      </header>

      {notice && <div className={`notice notice-${notice.kind}`}>{notice.text}</div>}
      {loadError && <div className="notice notice-error">{loadError}</div>}

      {dongle.state === 'ERROR' && <ErrorDetailPanel detail={dongle.last_error} />}

      {editing && (
        <EditConfigForm form={editForm} saving={saving} onChange={setEditForm} onSubmit={handleUpdate} />
      )}

      <div className="grid-layout">
        <div className="card">
          <h3 className="card-title">General Info</h3>
          <MetricRow label="State" value={<StateBadge state={dongle.state} />} />
          <MetricRow label="IMEI" value={<span className="mono">{dongle.identity_imei || '—'}</span>} />
          <MetricRow label="USB Serial" value={<span className="mono">{dongle.identity_usb_serial || '—'}</span>} />
          <MetricRow label="Interface" value={<span className="mono">{dongle.interface_name}</span>} />
          <MetricRow label="Route Table" value={<span className="mono">{dongle.route_table_id}</span>} />
          <MetricRow
            label="Reset endpoint"
            value={
              <div className="endpoint-copy">
                <span className="mono endpoint-url" title={resetUrl}>{resetUrl}</span>
                <button className="btn" onClick={() => endpointCopy.copy(resetUrl)}>
                  {endpointCopy.status === 'copied' ? 'Copied' : endpointCopy.status === 'failed' ? 'Failed' : 'Copy'}
                </button>
              </div>
            }
          />
          {endpointCopy.status === 'failed' && (
            <p className="endpoint-hint">Copy failed. Select the URL and copy it manually.</p>
          )}
        </div>

        <div className="card">
          <h3 className="card-title">Metrics</h3>
          <MetricRow label="Public IP" value={<span className="mono">{dongle.metrics?.public_ip || '—'}</span>} />
          <div className="metric-row">
            <span className="metric-label">Signal</span>
            <div className="signal-container">
              <div className="signal-bar-bg">
                <div className="signal-bar-fill" style={{ width: `${percent ?? 0}%`, background: signalTone(percent) }} />
              </div>
              <span className="mono">{percent != null ? `${percent}%` : '—'}</span>
            </div>
          </div>
          {signal?.rsrp != null && <MetricRow label="RSRP" value={<span className="mono">{signal.rsrp} dBm</span>} />}
          {signal?.rsrq != null && <MetricRow label="RSRQ" value={<span className="mono">{signal.rsrq} dB</span>} />}
          {signal?.sinr != null && <MetricRow label="SINR" value={<span className="mono">{signal.sinr} dB</span>} />}
          <MetricRow label="Avg Ping" value={<span className="mono">{dongle.metrics?.ping?.avg_ms ?? '—'} ms</span>} />
          <ProxyHealth
            health={dongle.metrics?.proxy_health ?? null}
            reason={dongle.metrics?.proxy_health_reason ?? null}
          />
        </div>

        <ProxyManagementCard dongleId={id} proxy={proxy} onChanged={loadData} actionsDisabled={editing} />
        {proxy && <ProxyAllowedHostsCard proxyId={proxy.id} containerName={proxy.container_name} />}
      </div>

      <ConfirmDialog
        open={deleteOpen}
        title="Delete dongle"
        message={`Delete “${dongle.label}”? This disconnects the dongle, removes its proxy and routing, and deletes the record. This cannot be undone.`}
        confirmLabel="Delete"
        busyLabel="Deleting…"
        destructive
        busy={deleting}
        error={deleteError}
        onConfirm={confirmDelete}
        onCancel={() => { if (!deleting) { setDeleteOpen(false); setDeleteError(null); } }}
      />
    </div>
  );
};
