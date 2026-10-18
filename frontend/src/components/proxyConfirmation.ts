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
    message: 'The proxy password is stored only as a hash and cannot be retrieved. Regenerating issues a new password, shown only once; existing clients are disconnected until updated with it.',
    confirmLabel: 'Regenerate',
    busyLabel: 'Regenerating…',
  },
};
