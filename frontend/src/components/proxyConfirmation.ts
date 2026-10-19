export type ProxyConfirmKind = 'delete' | 'rotate';

export const PROXY_CONFIRM: Record<ProxyConfirmKind, {
  title: string;
  message: string;
  confirmLabel: string;
  busyLabel: string;
  destructive?: boolean;
}> = {
  delete: {
    title: 'Delete proxy',
    message: 'Delete the 3proxy container for this dongle? The proxy routing and firewall rules are removed. This cannot be undone.',
    confirmLabel: 'Delete',
    busyLabel: 'Deleting…',
    destructive: true,
  },
  rotate: {
    title: 'Regenerate credentials',
    message: 'Regenerating immediately invalidates the current credentials: every configured client is disconnected until updated with the new password. The new password is shown now and can be viewed again later from the proxy card.',
    confirmLabel: 'Regenerate',
    busyLabel: 'Regenerating…',
  },
};
