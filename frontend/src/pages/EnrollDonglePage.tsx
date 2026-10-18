import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { errorMessage } from '../lib/errors';
import { DiscoveredDongle, DongleEnrollmentRequest, EnrollmentProxyCredentials } from '../types/api';
import { DonglePicker } from '../components/DonglePicker';
import { CredentialsDialog } from '../components/CredentialsDialog';
import { isPhoneTransport } from '../lib/transport';

export const EnrollDonglePage: React.FC = () => {
  const navigate = useNavigate();
  const [discovered, setDiscovered] = useState<DiscoveredDongle[]>([]);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [label, setLabel] = useState('');
  const [apn, setApn] = useState('');
  const [credentials, setCredentials] = useState<{ dongleId: string } & EnrollmentProxyCredentials | null>(null);

  const fetchDiscovered = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.dongles.discovered();
      setDiscovered(data);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const handleSelectDongle = (index: number) => {
    setSelectedIndex(index);
    setError(null);
    const d = discovered[index];
    if (d) {
      setLabel(d.suggested_label);
      setApn(isPhoneTransport(d.transport) ? '' : (d.suggested_apn ?? ''));
    }
  };

  useEffect(() => {
    fetchDiscovered();
  }, []);

  const selectedDongle = selectedIndex !== null ? discovered[selectedIndex] : null;
  const isPhone = selectedDongle ? isPhoneTransport(selectedDongle.transport) : false;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedDongle || submitting) return;

    setError(null);
    setSubmitting(true);
    const payload: DongleEnrollmentRequest = isPhone
      ? { label, transport: 'adb', identity_adb_serial: selectedDongle.identity_adb_serial }
      : {
          label,
          transport: 'modem',
          apn,
          identity_imei: selectedDongle.identity_imei,
          identity_usb_serial: selectedDongle.identity_usb_serial,
          identity_iccid: selectedDongle.identity_iccid,
        };
    try {
      const dongle = await api.dongles.create(payload);
      if (dongle.proxy_credentials) {
        // One-time credentials: show them before leaving this page.
        setCredentials({ dongleId: dongle.id, ...dongle.proxy_credentials });
        return;
      }
      navigate(`/dongles/${dongle.id}`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="main-content">
      <div className="section-title">
        <h1>Enroll New Dongle or Phone</h1>
        <button className="btn" onClick={() => navigate('/')}>← Back</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem', alignItems: 'start' }}>
        <section>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
            <h2>Detected Hardware</h2>
            <button className="btn" onClick={fetchDiscovered} disabled={loading}>
              {loading ? '...' : 'Refresh'}
            </button>
          </div>

          {loading && <p>Scanning for dongles and phones...</p>}
          {!loading && discovered.length === 0 && !error && (
            <div className="card" style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
              No unenrolled dongle or phone detected. Plug in a USB dongle or connect an Android
              phone with USB tethering, then refresh.
            </div>
          )}
          {!loading && error && selectedIndex === null && (
            <div style={{ color: 'var(--state-error)', marginBottom: '1rem', fontSize: '0.875rem' }}>
              {error}
            </div>
          )}
          {!loading && <DonglePicker dongles={discovered} selectedId={selectedIndex} onSelect={handleSelectDongle} />}
        </section>

        <section>
          <h2>Configuration</h2>
          <form onSubmit={handleSubmit} className="card enroll-form">
            {!selectedDongle ? (
              <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
                Select a detected dongle or phone to configure.
              </div>
            ) : (
              <>
                <div>
                  <label>Label *</label>
                  <input
                    className="input"
                    value={label}
                    onChange={e => setLabel(e.target.value)}
                    required
                  />
                </div>

                <div style={{ fontSize: '0.875rem', padding: '0.5rem', backgroundColor: 'var(--bg-input)', borderRadius: '4px' }}>
                  {isPhone ? (
                    <div><strong>ADB Serial:</strong> {selectedDongle.identity_adb_serial || '—'}</div>
                  ) : (
                    <>
                      <div><strong>IMEI:</strong> {selectedDongle.identity_imei || '—'}</div>
                      <div><strong>USB Serial:</strong> {selectedDongle.identity_usb_serial || '—'}</div>
                      <div><strong>ICCID:</strong> {selectedDongle.identity_iccid || '—'}</div>
                    </>
                  )}
                </div>

                {isPhone ? (
                  <div style={{ fontSize: '0.875rem', padding: '0.5rem', backgroundColor: 'var(--bg-input)', borderRadius: '4px', color: 'var(--text-muted)' }}>
                    A phone manages its own APN — there is nothing to set here.
                  </div>
                ) : (
                  <div>
                    <label>APN</label>
                    <input
                      className="input"
                      placeholder="internet"
                      value={apn}
                      onChange={e => setApn(e.target.value)}
                    />
                  </div>
                )}

                {error && <div style={{ color: 'var(--state-error)', fontSize: '0.875rem' }}>{error}</div>}

                {submitting && isPhone && (
                  <div style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>
                    This can take a few seconds — the phone is being switched to USB tethering and
                    its network is being set up.
                  </div>
                )}

                <button
                  className="btn btn-primary"
                  type="submit"
                  disabled={submitting || !label}
                >
                  {submitting ? 'Enrolling…' : isPhone ? 'Enroll Phone' : 'Enroll Dongle'}
                </button>
              </>
            )}
          </form>
        </section>
      </div>

      <CredentialsDialog
        open={credentials !== null}
        username={credentials?.username ?? ''}
        password={credentials?.password ?? ''}
        onClose={() => {
          if (credentials) navigate(`/dongles/${credentials.dongleId}`);
          setCredentials(null);
        }}
      />
    </div>
  );
};
