import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import { Dashboard } from './pages/Dashboard';
import { DongleDetailPage } from './pages/DongleDetailPage';
import { EnrollDonglePage } from './pages/EnrollDonglePage';
import { LocalUplinkPage } from './pages/LocalUplinkPage';
import { LocalUplinkDetailPage } from './pages/LocalUplinkDetailPage';
import { SettingsPage } from './pages/SettingsPage';
import { PoolsPage } from './features/pools/PoolsPage';
import { LoginOverlay } from './components/LoginOverlay';
import { api } from './lib/api';
import './index.css';

const App: React.FC = () => {
  const [showLogin, setShowLogin] = useState(false);

  useEffect(() => {
    const handleAuthRequired = () => setShowLogin(true);
    window.addEventListener('broxy4g-auth-required', handleAuthRequired);
    return () => window.removeEventListener('broxy4g-auth-required', handleAuthRequired);
  }, []);

  const handleLogin = async (user: string, pass: string) => {
    try {
      const credentials = btoa(`${user}:${pass}`);
      sessionStorage.setItem('broxy4g_auth', credentials);

      // Verify credentials against the authenticated session endpoint
      await api.session.verify();
      return true;
    } catch {
      sessionStorage.removeItem('broxy4g_auth');
      return false;
    }
  };

  return (
    <BrowserRouter>
      <div className="app-container">
        <aside className="sidebar">
          <div className="sidebar-brand">
            broxy4G
          </div>
          <nav className="sidebar-nav">
            <NavLink to="/" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              Dashboard
            </NavLink>
            <NavLink to="/pools" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              Pools
            </NavLink>
            <NavLink to="/settings" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              Configuration
            </NavLink>
          </nav>
        </aside>

        <main className="main-content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/pools" element={<PoolsPage />} />
            <Route path="/dongles/new" element={<EnrollDonglePage />} />
            <Route path="/dongles/:id" element={<DongleDetailPage />} />
            <Route path="/local/new" element={<LocalUplinkPage />} />
            <Route path="/local/:interface_name" element={<LocalUplinkDetailPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>

        {showLogin && (
          <LoginOverlay 
            onLogin={handleLogin} 
            onClose={() => setShowLogin(false)} 
          />
        )}
      </div>
    </BrowserRouter>
  );
};

export default App;
