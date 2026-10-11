import React from 'react';
import { DongleState, ProxyState } from '../types/api';

export const StateBadge: React.FC<{ state: DongleState | ProxyState }> = ({ state }) => {
  const colors: Record<string, string> = {
    ONLINE: 'var(--state-online)',
    DEGRADED: 'var(--state-degraded)',
    ERROR: 'var(--state-error)',
    OFFLINE: 'var(--state-offline)',
    CONNECTING: 'var(--state-connecting)',
    RESETTING: 'var(--state-resetting)',
    CONFIGURING: 'var(--state-configuring)',
    DISCOVERED: 'var(--state-discovered)',
    UNENROLLED: 'var(--state-unenrolled)',
    RUNNING: 'var(--state-online)',
    STARTING: 'var(--state-connecting)',
    STOPPED: 'var(--state-offline)',
    CRASHED: 'var(--state-error)',
    REMOVED: 'var(--state-offline)',
  };

  return (
    <span 
      className="badge" 
      style={{ 
        backgroundColor: colors[state] || 'var(--border-color)',
        color: 'white'
      }}
    >
      {state}
    </span>
  );
};
