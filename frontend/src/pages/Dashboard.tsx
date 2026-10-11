import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { errorMessage } from '../lib/errors';
import { useBatchReset } from '../lib/useBatchReset';
import { DongleSummary } from '../types/api';
import { DongleCard } from '../components/DongleCard';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { LocalUplinksSection } from '../features/uplinks/LocalUplinksSection';

type Notice = { kind: 'success' | 'warning' | 'error'; text: string } | null;

export const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const [dongles, setDongles] = useState<DongleSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [deleteTarget, setDeleteTarget] = useState<DongleSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const loadDongles = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.dongles.list();
      setDongles(data);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const onBatchSettled = useCallback(async () => {
    setNotice(null);
    await loadDongles();
  }, [loadDongles]);

  const batch = useBatchReset(dongles, onBatchSettled);

  useEffect(() => {
    loadDongles();
    const interval = setInterval(loadDongles, 10000);
    return () => clearInterval(interval);
  }, [loadDongles]);

  const handleReset = async (dongle: DongleSummary) => {
    setBusyId(dongle.id);
    setNotice(null);
    batch.dismissNotice();
    try {
      const res = await api.dongles.reset(dongle.id);
      if (res.status === 'PENDING') {
        setNotice({ kind: 'warning', text: `A reset is already in progress for “${dongle.label}”.` });
      } else if (res.status === 'ERROR') {
        setNotice({ kind: 'error', text: res.message || `Reset failed for “${dongle.label}”.` });
      } else {
        setNotice({ kind: 'success', text: `Reset completed for “${dongle.label}”.` });
      }
      await loadDongles();
    } catch (e) {
      setNotice({ kind: 'error', text: errorMessage(e) });
    } finally {
      setBusyId(null);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.dongles.delete(deleteTarget.id);
      setDeleteTarget(null);
      await loadDongles();
    } catch (e) {
      setDeleteError(errorMessage(e));
    } finally {
      setDeleting(false);
    }
  };

  const countLabel = `${dongles.length} enrolled ${dongles.length === 1 ? 'dongle' : 'dongles'}`;

  return (
    <div className="main-content">
      <header className="page-header">
        <div>
          <h1>Dongles</h1>
          <p className="page-subtitle">{countLabel}</p>
        </div>
        <div className="page-header-actions">
          <button
            className="btn"
            disabled={batch.selectedIds.size === 0 || batch.busy}
            onClick={batch.openConfirm}
          >
            {batch.selectedIds.size > 0 ? `Reset selected (${batch.selectedIds.size})` : 'Reset selected'}
          </button>
          <button className="btn" onClick={() => navigate('/local/new')}>
            + Enroll eth/wifi
          </button>
          <button className="btn btn-primary" onClick={() => navigate('/dongles/new')}>
            + Enroll Dongle
          </button>
        </div>
      </header>

      {notice && <div className={`notice notice-${notice.kind}`}>{notice.text}</div>}
      {batch.notice && (
        <div className={`notice notice-${batch.notice.kind}`}>
          <div>{batch.notice.text}</div>
          {batch.notice.failures.length > 0 && (
            <ul className="notice-list">
              {batch.notice.failures.map(f => (
                <li key={f.id}>{f.text}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && <div className="notice notice-error">{error}</div>}

      {loading && dongles.length === 0 ? (
        <div className="grid-layout">
          {[0, 1, 2].map(i => <div key={i} className="card dongle-card skeleton-card" />)}
        </div>
      ) : dongles.length === 0 ? (
        <div className="empty-state">
          <h2>No dongles enrolled</h2>
          <p>Plug in a USB dongle and enroll it to get started.</p>
          <button className="btn btn-primary" onClick={() => navigate('/dongles/new')}>
            + Enroll Dongle
          </button>
        </div>
      ) : (
        <>
          <div className="selection-bar">
            <label className="select-all">
              <input
                type="checkbox"
                ref={batch.selectAllRef}
                checked={batch.allSelected}
                disabled={batch.busy}
                onChange={batch.toggleSelectAll}
              />
              <span>Select all</span>
            </label>
            {batch.selectedIds.size > 0 && (
              <span className="selection-count">{batch.selectedIds.size} selected</span>
            )}
          </div>
          <div className="grid-layout">
            {dongles.map(d => (
              <DongleCard
                key={d.id}
                dongle={d}
                busy={busyId === d.id}
                batchBusy={batch.busy}
                selected={batch.selectedIds.has(d.id)}
                onSelect={() => batch.toggleSelect(d.id)}
                onViewDetails={() => navigate(`/dongles/${d.id}`)}
                onReset={() => handleReset(d)}
                onDelete={() => { setDeleteError(null); setDeleteTarget(d); }}
              />
            ))}
          </div>
        </>
      )}

      <LocalUplinksSection />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete dongle"
        message={deleteTarget
          ? `Delete “${deleteTarget.label}”? This disconnects the dongle, removes its proxy and routing, and deletes the record. This cannot be undone.`
          : ''}
        confirmLabel="Delete"
        busyLabel="Deleting…"
        destructive
        busy={deleting}
        error={deleteError}
        onConfirm={confirmDelete}
        onCancel={() => { if (!deleting) { setDeleteTarget(null); setDeleteError(null); } }}
      />

      <ConfirmDialog
        open={batch.confirmOpen}
        title="Reset selected dongles"
        message={`Reset ${batch.selectedIds.size} dongle${batch.selectedIds.size === 1 ? '' : 's'}? Each dongle will be disconnected and reconnected, briefly interrupting its uplink and proxy.`}
        confirmLabel={`Reset ${batch.selectedIds.size} dongle${batch.selectedIds.size === 1 ? '' : 's'}`}
        busyLabel="Resetting…"
        busy={batch.busy}
        error={batch.error}
        onConfirm={batch.confirmReset}
        onCancel={batch.closeConfirm}
      />
    </div>
  );
};
