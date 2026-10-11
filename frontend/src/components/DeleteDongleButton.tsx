import React, { useState } from 'react';
import { errorMessage } from '../lib/errors';

interface DeleteDongleButtonProps {
  onConfirm: () => Promise<void>;
  message?: string;
}

export const DeleteDongleButton: React.FC<DeleteDongleButtonProps> = ({ onConfirm, message }) => {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cancel = () => {
    setConfirming(false);
    setError(null);
  };

  const handleConfirm = async () => {
    setDeleting(true);
    setError(null);
    try {
      await onConfirm();
    } catch (e) {
      setError(errorMessage(e));
      setConfirming(false);
    } finally {
      setDeleting(false);
    }
  };

  if (confirming) {
    return (
      <span style={{ display: 'inline-flex', flexDirection: 'column', gap: '0.5rem', alignItems: 'flex-end' }}>
        {message && (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem', maxWidth: '320px' }}>{message}</span>
        )}
        <span style={{ display: 'inline-flex', gap: '0.5rem' }}>
          <button
            className="btn"
            style={{ color: 'var(--state-error)' }}
            onClick={handleConfirm}
            disabled={deleting}
          >
            {deleting ? 'Deleting...' : 'Confirm delete?'}
          </button>
          <button className="btn" onClick={cancel} disabled={deleting}>
            Cancel
          </button>
        </span>
      </span>
    );
  }

  return (
    <span style={{ display: 'inline-flex', gap: '0.5rem', alignItems: 'center' }}>
      <button
        className="btn"
        style={{ color: 'var(--state-error)' }}
        onClick={() => { setConfirming(true); setError(null); }}
      >
        Delete
      </button>
      {error && (
        <span style={{ color: 'var(--state-error)', fontSize: '0.8rem', maxWidth: '240px' }}>{error}</span>
      )}
    </span>
  );
};
