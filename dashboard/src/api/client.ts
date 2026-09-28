import type { LatestMetric, LatestMetricsResponse } from "./types";

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

export async function fetchLatestMetrics(host: string): Promise<LatestMetric[]> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/hosts/${encodeURIComponent(host)}/metrics/latest`);
  } catch {
    throw new ApiError("Could not reach the ingestion API.", 0);
  }

  if (response.status === 404) {
    throw new HostNotFoundError(host);
  }
  if (!response.ok) {
    throw new ApiError(`Request failed with status ${response.status}`, response.status);
  }

  const data = (await response.json()) as LatestMetricsResponse;
  return data.metrics;
}
