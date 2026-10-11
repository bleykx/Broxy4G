import React from 'react';
import { DiscoveredDongle } from '../types/api';

interface DonglePickerProps {
  dongles: DiscoveredDongle[];
  selectedId: number | null;
  onSelect: (index: number) => void;
}

export const DonglePicker: React.FC<DonglePickerProps> = ({ dongles, selectedId, onSelect }) => {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {dongles.map((dongle, index) => (
        <div 
          key={index} 
          onClick={() => onSelect(index)}
          className="card" 
          style={{ 
            cursor: 'pointer', 
            border: selectedId === index ? '2px solid var(--accent)' : '1px solid var(--border-color)',
            backgroundColor: selectedId === index ? 'var(--bg-surface)' : 'transparent',
            padding: '0.75rem',
            transition: 'all 0.2s ease'
          }}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '0.5rem', fontSize: '0.875rem' }}>
            <div><strong>IMEI:</strong> {dongle.identity_imei || '—'}</div>
            <div><strong>USB:</strong> {dongle.identity_usb_serial || '—'}</div>
            <div><strong>ICCID:</strong> {dongle.identity_iccid || '—'}</div>
            <div><strong>IF:</strong> {dongle.interface_name || '—'}</div>
          </div>
        </div>
      ))}
    </div>
  );
};
