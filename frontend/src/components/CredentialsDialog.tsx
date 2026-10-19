import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useCopyToClipboard, CopyStatus } from '../lib/useCopyToClipboard';

interface CredentialsDialogProps {
  open: boolean;
  username: string;
  password: string;
  message?: string;
  onClose: () => void;
}

const DEFAULT_MESSAGE =
  'These are the credentials of this proxy. You can view them again at any time from the proxy card.';

const copyLabel = (status: CopyStatus, base: string) =>
  status === 'copied' ? 'Copied' : status === 'failed' ? 'Failed' : base;

export const CredentialsDialog: React.FC<CredentialsDialogProps> = ({
  open,
  username,
  password,
  message,
  onClose,
}) => {
  const { status: userStatus, copy: copyUser, reset: resetUser } = useCopyToClipboard();
  const { status: passStatus, copy: copyPass, reset: resetPass } = useCopyToClipboard();
  const { status: bothStatus, copy: copyBoth, reset: resetBoth } = useCopyToClipboard();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    resetUser();
    resetPass();
    resetBoth();
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose, resetUser, resetPass, resetBoth]);

  if (!open) return null;

  const copyFailed =
    userStatus === 'failed' || passStatus === 'failed' || bothStatus === 'failed';

  // Mounted on document.body so a transformed ancestor (e.g. .card:hover)
  // cannot become the containing block of this fixed overlay.
  return createPortal(
    <div className="dialog-overlay" onMouseDown={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="credentials-dialog-title"
        onMouseDown={e => e.stopPropagation()}
      >
        <h2 id="credentials-dialog-title" className="dialog-title">Proxy credentials</h2>
        <p className="dialog-message">{message ?? DEFAULT_MESSAGE}</p>
        <div className="credentials">
          <div className="credentials-row">
            <span className="metric-label">Username</span>
            <span className="mono">{username}</span>
            <button className="btn" onClick={() => copyUser(username)}>
              {copyLabel(userStatus, 'Copy')}
            </button>
          </div>
          <div className="credentials-row">
            <span className="metric-label">Password</span>
            <span className="mono">{password}</span>
            <button className="btn" onClick={() => copyPass(password)}>
              {copyLabel(passStatus, 'Copy')}
            </button>
          </div>
          <div className="credentials-row">
            <span className="metric-label">User:pass</span>
            <span className="mono">{`${username}:${password}`}</span>
            <button className="btn btn-primary" onClick={() => copyBoth(`${username}:${password}`)}>
              {copyLabel(bothStatus, 'Copy both')}
            </button>
          </div>
        </div>
        {copyFailed && (
          <p className="dialog-error">Copy failed. Select the value and copy it manually.</p>
        )}
        <div className="dialog-actions">
          <button ref={closeRef} className="btn btn-primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>,
    document.body,
  );
};
