import React from 'react';
import { LocalLinkState } from '../../types/api';

const LINK_STATE_TONES: Record<LocalLinkState, string> = {
  up: 'var(--state-online)',
  down: 'var(--state-error)',
  unknown: 'var(--border-color)',
};

export const LinkStateBadge: React.FC<{ state: LocalLinkState }> = ({ state }) => (
  <span
    className="badge"
    style={{ backgroundColor: LINK_STATE_TONES[state], color: 'white' }}
  >
    {state}
  </span>
);
