import React, { useEffect, useRef, useState } from 'react';

interface DongleActionsMenuProps {
  onViewDetails: () => void;
  onReset: () => void;
  onDelete: () => void;
  disabled?: boolean;
}

export const DongleActionsMenu: React.FC<DongleActionsMenuProps> = ({
  onViewDetails,
  onReset,
  onDelete,
  disabled = false,
}) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };

    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const first = menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]');
    first?.focus();
  }, [open]);

  const handleMenuKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [],
    );
    if (items.length === 0) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const delta = e.key === 'ArrowDown' ? 1 : -1;
    items[(current + delta + items.length) % items.length]?.focus();
  };

  const select = (action: () => void) => {
    setOpen(false);
    action();
  };

  return (
    <div className="dropdown" ref={rootRef} onKeyDown={handleMenuKeyDown}>
      <button
        ref={buttonRef}
        type="button"
        className="kebab-btn"
        aria-label="Dongle options"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen(v => !v)}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <circle cx="12" cy="5" r="2" />
          <circle cx="12" cy="12" r="2" />
          <circle cx="12" cy="19" r="2" />
        </svg>
      </button>

      {open && (
        <div className="dropdown-menu" role="menu" ref={menuRef}>
          <button type="button" role="menuitem" className="dropdown-item" onClick={() => select(onViewDetails)}>
            View details
          </button>
          <button type="button" role="menuitem" className="dropdown-item" onClick={() => select(onReset)}>
            Reset
          </button>
          <div className="dropdown-sep" />
          <button type="button" role="menuitem" className="dropdown-item danger" onClick={() => select(onDelete)}>
            Delete…
          </button>
        </div>
      )}
    </div>
  );
};
