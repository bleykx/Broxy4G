import React, { useEffect, useRef, useState } from 'react';

interface CredentialsDialogProps {
  open: boolean;
  username: string;
  password: string;
  onClose: () => void;
}

export const CredentialsDialog: React.FC<CredentialsDialogProps> = ({ open, username, password, onClose }) => {
  const [copied, setCopied] = useState<'user' | 'pass' | 'both' | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    setCopied(null);
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const copy = async (value: string, which: 'user' | 'pass' | 'both') => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(which);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      // Clipboard unavailable: the values remain selectable below.
    }
  };

  return (
    <div className="dialog-overlay" onMouseDown={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="credentials-dialog-title"
        onMouseDown={e => e.stopPropagation()}
      >
        <h2 id="credentials-dialog-title" className="dialog-title">Proxy credentials</h2>
        <p className="dialog-message">
          Store these credentials now. The password is shown only this once and will not be available again.
        </p>
        <div className="credentials">
          <div className="credentials-row">
            <span className="metric-label">Username</span>
            <span className="mono">{username}</span>
            <button className="btn" onClick={() => copy(username, 'user')}>
              {copied === 'user' ? 'Copied' : 'Copy'}
            </button>
          </div>
          <div className="credentials-row">
            <span className="metric-label">Password</span>
            <span className="mono">{password}</span>
            <button className="btn" onClick={() => copy(password, 'pass')}>
              {copied === 'pass' ? 'Copied' : 'Copy'}
            </button>
          </div>
          <div className="credentials-row">
            <span className="metric-label">User:pass</span>
            <span className="mono">{`${username}:${password}`}</span>
            <button className="btn btn-primary" onClick={() => copy(`${username}:${password}`, 'both')}>
              {copied === 'both' ? 'Copied' : 'Copy both'}
            </button>
          </div>
        </div>
        <div className="dialog-actions">
          <button ref={closeRef} className="btn btn-primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
};
