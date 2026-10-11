import React from 'react';

export const MetricRow: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="metric-row">
    <span className="metric-label">{label}</span>
    {value}
  </div>
);
