import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { errorMessage } from './errors';
import { DongleSummary, ResetBatchItem } from '../types/api';

export type BatchNotice = {
  kind: 'success' | 'warning' | 'error';
  text: string;
  failures: { id: string; text: string }[];
} | null;

// Selection, confirmation, execution and summary of the batch dongle reset.
export function useBatchReset(dongles: DongleSummary[], onSettled: () => Promise<void>) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<BatchNotice>(null);
  const selectAllRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const valid = new Set(dongles.map(d => d.id));
    setSelectedIds(prev => {
      const next = new Set(Array.from(prev).filter(id => valid.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [dongles]);

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate =
        selectedIds.size > 0 && selectedIds.size < dongles.length;
    }
  }, [selectedIds, dongles.length]);

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedIds(prev =>
      prev.size === dongles.length
        ? new Set<string>()
        : new Set(dongles.map(d => d.id)),
    );
  };

  const summarize = (results: ResetBatchItem[]): BatchNotice => {
    const done = results.filter(r => r.status === 'DONE').length;
    const pending = results.filter(r => r.status === 'PENDING').length;
    const locked = results.filter(r => r.status === 'LOCK').length;
    const failed = results.filter(r => r.status === 'ERROR');
    const labelOf = (id: string) => dongles.find(d => d.id === id)?.label ?? id;
    const failures = failed.map(r => ({
      id: r.dongle_id,
      text: `“${labelOf(r.dongle_id)}” — ${r.message ?? 'reset failed'}`,
    }));
    const parts: string[] = [];
    if (done > 0) parts.push(`${done} done`);
    if (pending > 0) parts.push(`${pending} pending`);
    if (locked > 0) parts.push(`${locked} locked`);
    if (failed.length > 0) parts.push(`${failed.length} failed`);
    return {
      kind: failed.length > 0 ? 'error' : pending > 0 || locked > 0 ? 'warning' : 'success',
      text: `Batch reset finished: ${parts.join(', ')}.`,
      failures,
    };
  };

  const confirmReset = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.dongles.resetMany(ids);
      setNotice(summarize(res.results));
      setSelectedIds(new Set());
      setConfirmOpen(false);
      await onSettled();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return {
    selectedIds,
    allSelected: dongles.length > 0 && selectedIds.size === dongles.length,
    selectAllRef,
    toggleSelect,
    toggleSelectAll,
    confirmOpen,
    openConfirm: () => { setError(null); setConfirmOpen(true); },
    closeConfirm: () => { if (!busy) { setConfirmOpen(false); setError(null); } },
    busy,
    error,
    confirmReset,
    notice,
    dismissNotice: () => setNotice(null),
  };
}
