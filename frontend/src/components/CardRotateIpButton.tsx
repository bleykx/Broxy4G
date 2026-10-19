import React, { useState } from 'react';
import { api } from '../lib/api';
import { errorMessage } from '../lib/errors';
import { ConfirmDialog } from './ConfirmDialog';
import { DongleState, DongleSummary } from '../types/api';

export type CardResetNotice = { kind: 'success' | 'warning' | 'error'; text: string };

const blockedTitle = (state: DongleState): string => {
  if (state === 'CONFIGURING') return 'The dongle is still configuring; wait for it to come online.';
  if (state === 'RESETTING') return 'A reset is already in progress.';
  return 'Enroll this dongle before rotating its IP.';
};

interface CardRotateIpButtonProps {
  dongle: DongleSummary;
  /** Reset in flight from the card menu or a batch reset. */
  externalBusy: boolean;
  onBusyChange: (busy: boolean) => void;
  onNotice: (notice: CardResetNotice) => void;
  onSettled: () => void;
}

export const CardRotateIpButton: React.FC<CardRotateIpButtonProps> = ({
  dongle,
  externalBusy,
  onBusyChange,
  onNotice,
  onSettled,
}) => {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const blocked =
    dongle.state === 'DISCOVERED' ||
    dongle.state === 'UNENROLLED' ||
    dongle.state === 'CONFIGURING' ||
    dongle.state === 'RESETTING';
  const disabled = blocked || externalBusy || busy;
  const title = blocked
    ? blockedTitle(dongle.state)
    : externalBusy
      ? 'A reset is already in progress.'
      : undefined;

  const confirm = async () => {
    setBusy(true);
    setError(null);
    onBusyChange(true);
    try {
      const res = await api.dongles.reset(dongle.id);
      setOpen(false);
      if (res.status === 'PENDING') {
        onNotice({ kind: 'warning', text: `A reset is already in progress for “${dongle.label}”.` });
      } else if (res.status === 'LOCK') {
        onNotice({ kind: 'warning', text: `This proxy is locked after a recent reset; try again later or unlock it.` });
      } else if (res.status === 'ERROR') {
        onNotice({ kind: 'error', text: res.message || `Reset failed for “${dongle.label}”.` });
      } else {
        onNotice({ kind: 'success', text: `Reset completed for “${dongle.label}”.` });
      }
      onSettled();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className="btn btn-sm"
        title={title}
        disabled={disabled}
        onClick={() => { setError(null); setOpen(true); }}
      >
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M21 12a9 9 0 1 1-2.64-6.36" />
          <path d="M21 3v6h-6" />
        </svg>
        Rotate IP
      </button>
      <ConfirmDialog
        open={open}
        title="Rotate IP"
        message={`Rotate the IP of “${dongle.label}”? The dongle will be disconnected and reconnected, briefly interrupting its uplink and proxy.`}
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
