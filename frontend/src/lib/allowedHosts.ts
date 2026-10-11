const IPV4_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;

const HINT =
  'Use an IPv4 address (203.0.113.10), a CIDR block (10.0.0.0/24) or a range (192.168.1.10-192.168.1.99).';

type Ipv4Parts = [number, number, number, number];

function ipv4Parts(value: string): Ipv4Parts | null {
  const m = IPV4_RE.exec(value);
  if (!m) return null;
  const parts = m.slice(1, 5).map(Number);
  if (parts.some(p => p > 255)) return null;
  return parts as Ipv4Parts;
}

function ipv4ToNumber(parts: Ipv4Parts): number {
  return ((parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]) >>> 0;
}

/** Returns a human-readable error, or null when the value is a valid allowed host. */
export function validateAllowedHost(raw: string): string | null {
  const value = raw.trim();
  if (!value) {
    return `Enter a host first. ${HINT}`;
  }

  const cidr = /^(\S+)\/(\d{1,2})$/.exec(value);
  if (cidr) {
    if (!ipv4Parts(cidr[1])) {
      return `Invalid CIDR prefix "${cidr[1]}". ${HINT}`;
    }
    if (Number(cidr[2]) > 32) {
      return `CIDR length ${cidr[2]} is out of range (0-32). ${HINT}`;
    }
    return null;
  }

  const range = /^(\S+)-(\S+)$/.exec(value);
  if (range) {
    const start = ipv4Parts(range[1]);
    const end = ipv4Parts(range[2]);
    if (!start || !end) {
      return `Invalid range "${value}". ${HINT}`;
    }
    if (ipv4ToNumber(start) > ipv4ToNumber(end)) {
      return 'Range start must be lower than or equal to its end.';
    }
    return null;
  }

  if (ipv4Parts(value)) {
    return null;
  }
  return `Invalid format. ${HINT}`;
}
