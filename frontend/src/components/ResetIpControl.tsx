import React, { useState } from 'react';
import { api } from '../lib/api';
import { errorMessage } from '../lib/errors';
import { ConfirmDialog } from './ConfirmDialog';

export type ResetNotice = { kind: 'success' | 'warning' | 'error'; text: string };

interface ResetIpControlProps {
  dongleId: string;
  dongleLabel: string;
  onChanged: () => void;
  onNotice: (notice: ResetNotice) => void;
}

export const ResetIpControl: React.FC<ResetIpControlProps> = ({ dongleId, dongleLabel, onChanged, onNotice }) => {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.dongles.reset(dongleId);
      setOpen(false);
      if (res.status === 'PENDING') {
        onNotice({ kind: 'warning', text: 'A reset is already in progress for this dongle.' });
      } else if (res.status === 'LOCK') {
        onNotice({ kind: 'warning', text: 'This proxy is locked after a recent reset; try again later or unlock it.' });
      } else if (res.status === 'ERROR') {
        onNotice({ kind: 'error', text: res.message || 'Reset failed.' });
      } else {
        onNotice({ kind: 'success', text: 'Reset completed. The dongle is reconnecting.' });
      }
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button className="btn" disabled={busy} onClick={() => { setError(null); setOpen(true); }}>
        Rotate IP
      </button>
      <ConfirmDialog
        open={open}
        title="Rotate IP"
        message={`Rotate the IP of “${dongleLabel}”? The dongle will be disconnected and reconnected, briefly interrupting its uplink and proxy.`}
        confirmLabel="Rotate IP"
        busyLabel="Rotating…"
        busy={busy}
        error={error}
        onConfirm={confirm}
        onCancel={() => { if (!busy) { setOpen(false); setError(null); } }}
      />
    </>
  );
};
