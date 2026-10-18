import React from 'react';
import { isPhoneTransport } from '../lib/transport';

export const TransportBadge: React.FC<{ transport: string }> = ({ transport }) => {
  const phone = isPhoneTransport(transport);
  return (
    <span
      className="badge"
      title={phone ? 'Android phone via USB tethering' : '4G/5G USB dongle'}
      style={{
        backgroundColor: phone ? 'var(--state-connecting)' : 'var(--state-discovered)',
        color: 'white',
      }}
    >
      {phone ? 'Phone' : '4G'}
    </span>
  );
};
