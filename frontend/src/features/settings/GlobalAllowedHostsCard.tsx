import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../../lib/api';
import { errorMessage } from '../../lib/errors';
import { AllowedHost } from '../../types/api';
import { AllowedHostsEditor } from '../../components/AllowedHostsEditor';

export const GlobalAllowedHostsCard: React.FC = () => {
  const [hosts, setHosts] = useState<AllowedHost[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setHosts(await api.allowedHosts.list());
      setLoadError(null);
    } catch (e) {
      setLoadError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="card settings-card">
      <h3 className="card-title">Global Allowed Hosts</h3>
      <p className="hosts-scope">
        These addresses may use <strong>every</strong> proxy, in addition to each proxy's own
        list. A change is applied immediately and restarts all proxies.
      </p>
      <div className="notice notice-warning hosts-host-note">
        The host's own loopback address and the Docker gateway are always allowed and cannot be
        edited here.
      </div>
      {loadError && <p className="card-error">{loadError}</p>}
      {loading ? (
        <p className="hosts-loading">Loading…</p>
      ) : (
        <AllowedHostsEditor
          hosts={hosts.map(h => h.host)}
          onChanged={load}
          add={host => api.allowedHosts.add(host)}
          remove={host => api.allowedHosts.remove(host)}
          placeholder="e.g. 203.0.113.10, 10.0.0.0/24 or 192.168.1.10-192.168.1.99"
          emptyText="No global hosts yet. Add an IPv4 address, a CIDR block or a range to allow it on every proxy."
          removeTitle="Remove global host"
          removeMessage={host =>
            `Remove "${host}" from the global list? Every proxy loses access to it immediately.`}
          appliedNote="All proxies were restarted and may be briefly unavailable."
        />
      )}
    </div>
  );
};
