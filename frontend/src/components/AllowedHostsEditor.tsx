import React, { useState } from 'react';
import { errorMessage } from '../lib/errors';
import { validateAllowedHost } from '../lib/allowedHosts';
import { ConfirmDialog } from './ConfirmDialog';

interface AllowedHostsEditorProps {
  hosts: string[];
  onChanged: () => void;
  add: (host: string) => Promise<unknown>;
  remove: (host: string) => Promise<unknown>;
  placeholder: string;
  emptyText: string;
  removeTitle: string;
  removeMessage: (host: string) => string;
  /** Shown after a successful add/remove: what the server restarted. */
  appliedNote: string;
}

type Notice = { kind: 'success' | 'error'; text: string } | null;

export const AllowedHostsEditor: React.FC<AllowedHostsEditorProps> = ({
  hosts,
  onChanged,
  add,
  remove,
  placeholder,
  emptyText,
  removeTitle,
  removeMessage,
  appliedNote,
}) => {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const host = value.trim();
    const invalid = validateAllowedHost(host);
    if (invalid) {
      setError(invalid);
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await add(host);
      setValue('');
      setNotice({ kind: 'success', text: `"${host}" added. ${appliedNote}` });
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    if (!removeTarget) return;
    setRemoving(true);
    setRemoveError(null);
    try {
      await remove(removeTarget);
      setNotice({ kind: 'success', text: `"${removeTarget}" removed. ${appliedNote}` });
      setRemoveTarget(null);
      onChanged();
    } catch (err) {
      setRemoveError(errorMessage(err));
    } finally {
      setRemoving(false);
    }
  };

  const locked = busy || removing;

  return (
    <div>
      <form className="hosts-add-form" onSubmit={handleAdd}>
        <input
          className="input mono"
          value={value}
          onChange={e => { setValue(e.target.value); setError(null); }}
          placeholder={placeholder}
          disabled={locked}
          aria-label="New allowed host"
        />
        <button className="btn btn-primary" type="submit" disabled={locked}>
          {busy ? 'Applying…' : 'Add'}
        </button>
      </form>

      {error && <p className="card-error">{error}</p>}
      {notice && <div className={`notice notice-${notice.kind} hosts-notice`}>{notice.text}</div>}

      {hosts.length === 0 ? (
        <p className="hosts-empty">{emptyText}</p>
      ) : (
        <ul className="hosts-list">
          {hosts.map(host => (
            <li key={host} className="hosts-row">
              <span className="mono hosts-host" title={host}>{host}</span>
              <button
                className="btn btn-danger-text hosts-remove"
                disabled={locked}
                onClick={() => { setRemoveError(null); setRemoveTarget(host); }}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={removeTarget !== null}
        title={removeTitle}
        message={removeTarget ? removeMessage(removeTarget) : ''}
        confirmLabel="Remove"
        busyLabel="Removing…"
        destructive
        busy={removing}
        error={removeError}
        onConfirm={handleRemove}
        onCancel={() => { if (!removing) { setRemoveTarget(null); setRemoveError(null); } }}
      />
    </div>
  );
};
