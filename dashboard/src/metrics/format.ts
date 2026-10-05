export function formatPercent(value: number): string {
  return `${value.toFixed(1)}%`;
}

export function formatMb(mb: number): string {
  if (mb >= 1024) {
    return `${(mb / 1024).toFixed(1)} GB`;
  }
  return `${mb.toFixed(0)} MB`;
}

export function formatMbps(mbPerSecond: number): string {
  return `${mbPerSecond.toFixed(2)} MB/s`;
}

/** A short "how long ago" string, e.g. "5s ago", "3h ago". `now` defaults to
 * the real current time but is overridable so this is deterministically
 * testable. */
export function formatRelativeTime(epochSeconds: number, now: number = Date.now() / 1000): string {
  const diff = Math.max(0, now - epochSeconds);
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
