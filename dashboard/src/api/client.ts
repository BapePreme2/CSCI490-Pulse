import type { HistoryResponse, LatestMetric, LatestMetricsResponse } from "./types";

const DEFAULT_API_BASE_URL = "http://localhost:8000";

export function apiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL;
}

export class HostNotFoundError extends Error {
  constructor(host: string) {
    super(`Unknown host: ${host}`);
    this.name = "HostNotFoundError";
  }
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson<T>(path: string, host: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`);
  } catch {
    throw new ApiError("Could not reach the ingestion API.", 0);
  }

  if (response.status === 404) {
    throw new HostNotFoundError(host);
  }
  if (!response.ok) {
    throw new ApiError(`Request failed with status ${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

export async function fetchLatestMetrics(host: string): Promise<LatestMetric[]> {
  const data = await getJson<LatestMetricsResponse>(
    `/hosts/${encodeURIComponent(host)}/metrics/latest`,
    host,
  );
  return data.metrics;
}

export async function fetchMetricHistory(
  host: string,
  name: string,
  start?: number,
  end?: number,
): Promise<HistoryResponse> {
  const params = new URLSearchParams();
  if (start !== undefined) params.set("start", String(start));
  if (end !== undefined) params.set("end", String(end));
  const query = params.toString() ? `?${params.toString()}` : "";

  return getJson<HistoryResponse>(
    `/hosts/${encodeURIComponent(host)}/metrics/${encodeURIComponent(name)}${query}`,
    host,
  );
}
