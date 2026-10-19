import React from 'react';

interface ProxyActionsProps {
  busy: string | null;
  disabled: boolean;
  onStart: () => void;
  onStop: () => void;
  onRestart: () => void;
  onViewCredentials: () => void;
  onRotate: () => void;
  onDelete: () => void;
}

export const ProxyActions: React.FC<ProxyActionsProps> = ({
  busy,
  disabled,
  onStart,
  onStop,
  onRestart,
  onViewCredentials,
  onRotate,
  onDelete,
}) => (
  <div className="proxy-actions">
    <button className="btn" disabled={disabled} onClick={onStart}>
      {busy === 'start' ? 'Starting…' : 'Start'}
    </button>
    <button className="btn" disabled={disabled} onClick={onStop}>
      {busy === 'stop' ? 'Stopping…' : 'Stop'}
    </button>
    <button className="btn" disabled={disabled} onClick={onRestart}>
      {busy === 'restart' ? 'Restarting…' : 'Restart'}
    </button>
    <button className="btn btn-subtle" disabled={disabled} onClick={onViewCredentials}>
      {busy === 'view-credentials' ? 'Loading…' : 'View credentials'}
    </button>
    <button className="btn" disabled={disabled} onClick={onRotate}>
      Regenerate credentials
    </button>
    <button className="btn btn-danger-text" disabled={disabled} onClick={onDelete}>
      Delete
    </button>
  </div>
);
