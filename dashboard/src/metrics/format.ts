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
