import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../../lib/api';
import { errorMessage } from '../../lib/errors';
import { AllowedHost } from '../../types/api';
import { AllowedHostsEditor } from '../../components/AllowedHostsEditor';

interface ProxyAllowedHostsCardProps {
  proxyId: string;
  containerName: string;
}

export const ProxyAllowedHostsCard: React.FC<ProxyAllowedHostsCardProps> = ({ proxyId, containerName }) => {
  const [hosts, setHosts] = useState<AllowedHost[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setHosts(await api.allowedHosts.listForProxy(proxyId));
      setLoadError(null);
    } catch (e) {
      setLoadError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [proxyId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="card">
      <h3 className="card-title">Proxy Allowed Hosts</h3>
      <p className="hosts-scope">
        Addresses allowed to use <strong>{containerName}</strong> only, in addition to the global
        list and the host's own addresses. A change is applied immediately and restarts this proxy.
      </p>
      {loadError && <p className="card-error">{loadError}</p>}
      {loading ? (
        <p className="hosts-loading">Loading…</p>
      ) : (
        <AllowedHostsEditor
          hosts={hosts.map(h => h.host)}
          onChanged={load}
          add={host => api.allowedHosts.addForProxy(proxyId, host)}
          remove={host => api.allowedHosts.removeForProxy(proxyId, host)}
          placeholder="e.g. 203.0.113.10, 10.0.0.0/24 or 192.168.1.10-192.168.1.99"
          emptyText="No proxy-specific hosts yet. Add an IPv4 address, a CIDR block or a range for this proxy."
          removeTitle="Remove proxy host"
          removeMessage={host =>
            `Remove "${host}" from ${containerName}? This proxy loses access to it immediately.`}
          appliedNote="This proxy was restarted and may be briefly unavailable."
        />
      )}
    </div>
  );
};
