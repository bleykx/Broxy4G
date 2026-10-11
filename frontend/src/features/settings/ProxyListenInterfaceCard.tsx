import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../../lib/api';
import { errorMessage } from '../../lib/errors';
import { LocalConnection, ProxyListenInterfaceSetting } from '../../types/api';

const KIND_COLORS: Record<string, string> = {
  ethernet: 'var(--state-connecting)',
  wifi: 'var(--state-configuring)',
};

export const ProxyListenInterfaceCard: React.FC = () => {
  const [setting, setSetting] = useState<ProxyListenInterfaceSetting | null>(null);
  const [connections, setConnections] = useState<LocalConnection[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [current, list] = await Promise.all([
        api.settings.getListenInterface(),
        api.localConnections.list(),
      ]);
      setSetting(current);
      setConnections(list);
      setSelected(current.interface_name);
    } catch (e) {
      setLoadError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const configured = setting?.interface_name ?? null;
  const eligible = connections.filter(c => c.kind !== 'other');
  const configuredMissing = configured !== null &&
    !eligible.some(c => c.interface_name === configured);
  const dirty = !loading && selected !== configured;

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    setSaved(null);
    try {
      const result = await api.settings.setListenInterface(selected);
      setSetting(result);
      setSelected(result.interface_name);
      setSaved(
        `Proxies now listen on ${result.interface_name ?? 'all interfaces'}. ` +
        'Active proxies were restarted and may be briefly unavailable.',
      );
    } catch (e) {
      setSaveError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card settings-card">
      <h3 className="card-title">Proxy Listen Interface</h3>
      <p className="hosts-scope">
        Host interface on which the proxy ports are published. Clients reach the proxies at
        the address of this interface. When no interface is set, proxies listen on all
        interfaces.
      </p>
      <div className="notice notice-warning">
        Saving restarts every active proxy immediately. Each proxy is briefly unavailable
        while it restarts.
      </div>
      {loadError && <p className="card-error">{loadError}</p>}
      {loading ? (
        <p className="hosts-loading">Loading…</p>
      ) : (
        <>
          {configuredMissing && (
            <div className="notice notice-warning">
              The configured interface <span className="mono">{configured}</span> is not
              currently present on this host.
            </div>
          )}
          {eligible.length === 0 && configured === null ? (
            <p className="hosts-empty">
              No ethernet or wifi interface is currently available on this host.
            </p>
          ) : (
            <div className="listen-options">
              <label className="listen-option">
                <input
                  type="radio"
                  name="proxy-listen-interface"
                  value=""
                  checked={selected === null}
                  disabled={saving}
                  onChange={() => setSelected(null)}
                />
                <span className="listen-option-name">All interfaces</span>
                <span className="listen-option-hint">default, reachable on every address</span>
              </label>
              {eligible.map(c => (
                <label className="listen-option" key={c.interface_name}>
                  <input
                    type="radio"
                    name="proxy-listen-interface"
                    value={c.interface_name}
                    checked={selected === c.interface_name}
                    disabled={saving}
                    onChange={() => setSelected(c.interface_name)}
                  />
                  <span className="listen-option-name mono">{c.interface_name}</span>
                  <span
                    className="badge"
                    style={{ backgroundColor: KIND_COLORS[c.kind], color: 'white' }}
                  >
                    {c.kind}
                  </span>
                  <span className="listen-option-ip mono">{c.ip ?? '—'}</span>
                </label>
              ))}
              {configuredMissing && (
                <label className="listen-option listen-option-missing">
                  <input
                    type="radio"
                    name="proxy-listen-interface"
                    value={configured as string}
                    checked={selected === configured}
                    disabled
                  />
                  <span className="listen-option-name mono">{configured}</span>
                  <span className="listen-option-hint">not currently present</span>
                </label>
              )}
            </div>
          )}
          {saveError && <p className="card-error">{saveError}</p>}
          {saved && <div className="notice notice-success">{saved}</div>}
          <div className="listen-actions">
            <button className="btn btn-primary" disabled={!dirty || saving} onClick={handleSave}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button className="btn" onClick={load} disabled={saving}>Refresh</button>
          </div>
        </>
      )}
    </div>
  );
};
