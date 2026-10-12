import { useCallback, useEffect, useRef, useState } from 'react';

export type CopyStatus = 'idle' | 'copied' | 'failed';

const COPIED_RESET_MS = 1500;
const FAILED_RESET_MS = 4000;

// Async clipboard API first, then the legacy selection path for
// non-secure contexts (HTTP on a LAN IP) where navigator.clipboard is absent.
async function writeClipboard(value: string): Promise<boolean> {
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // Permission denied or rejected: fall through to the legacy path.
    }
  }
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  document.body.removeChild(textarea);
  return ok;
}

// Copy-to-clipboard with visible feedback: 'copied' or 'failed' (value stays selectable).
export function useCopyToClipboard() {
  const [status, setStatus] = useState<CopyStatus>('idle');
  const timerRef = useRef<number | null>(null);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(() => clearTimer, []);

  const reset = useCallback(() => {
    clearTimer();
    setStatus('idle');
  }, []);

  const copy = useCallback(async (value: string) => {
    clearTimer();
    const ok = await writeClipboard(value);
    setStatus(ok ? 'copied' : 'failed');
    timerRef.current = window.setTimeout(() => setStatus('idle'), ok ? COPIED_RESET_MS : FAILED_RESET_MS);
  }, []);

  return { status, copy, reset };
}
