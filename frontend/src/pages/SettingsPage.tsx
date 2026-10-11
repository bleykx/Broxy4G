import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlobalAllowedHostsCard } from '../features/settings/GlobalAllowedHostsCard';
import { ProxyListenInterfaceCard } from '../features/settings/ProxyListenInterfaceCard';

export const SettingsPage: React.FC = () => {
  const navigate = useNavigate();

  return (
    <div className="main-content">
      <header className="page-header">
        <div>
          <h1>Configuration</h1>
          <p className="page-subtitle">
            Global access rules and proxy network settings. Per-proxy hosts are edited on
            each dongle's detail page.
          </p>
        </div>
        <div className="page-header-actions">
          <button className="btn" onClick={() => navigate('/')}>← Back</button>
        </div>
      </header>

      <GlobalAllowedHostsCard />
      <ProxyListenInterfaceCard />
    </div>
  );
};
