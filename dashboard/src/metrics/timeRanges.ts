export interface TimeRangeOption {
  label: string;
  seconds: number;
}

export const TIME_RANGES: TimeRangeOption[] = [
  { label: "1h", seconds: 3600 },
  { label: "6h", seconds: 6 * 3600 },
  { label: "24h", seconds: 24 * 3600 },
  { label: "7d", seconds: 7 * 24 * 3600 },
];

export const DEFAULT_TIME_RANGE: TimeRangeOption = TIME_RANGES[0];
