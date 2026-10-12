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
    title: 'View credentials',
    message: 'The password was never stored, so viewing it regenerates a new one. Existing clients are disconnected until updated with the new password, which is shown only once.',
    confirmLabel: 'Regenerate',
    busyLabel: 'Regenerating…',
  },
};
