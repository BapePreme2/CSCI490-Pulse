import type {
  AlertRule,
  AlertRuleInput,
  AlertRulesResponse,
  FleetHost,
  FleetHostsResponse,
  HistoryResponse,
  HostSummary,
  LatestMetric,
  LatestMetricsResponse,
} from "./types";

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

/** One field-level problem from a 422, e.g. `{field: "threshold", message: "..."}`. */
export interface FieldError {
  field: string;
  message: string;
}

/** The API rejected a rule (422). `errors` lists each field it objected to. */
export class RuleValidationError extends ApiError {
  errors: FieldError[];

  constructor(errors: FieldError[]) {
    super("The API rejected this rule.", 422);
    this.name = "RuleValidationError";
    this.errors = errors;
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

export async function fetchHosts(): Promise<FleetHost[]> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/hosts`);
  } catch {
    throw new ApiError("Could not reach the ingestion API.", 0);
  }
  if (!response.ok) {
    throw new ApiError(`Request failed with status ${response.status}`, response.status);
  }
  const data = (await response.json()) as FleetHostsResponse;
  return data.hosts;
}

export async function fetchHostSummary(host: string): Promise<HostSummary> {
  return getJson<HostSummary>(`/hosts/${encodeURIComponent(host)}`, host);
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

// FastAPI's 422 body: {"detail": [{"loc": ["body", "threshold"], "msg": "..."}, ...]}.
// The field is the top-level one after "body", so a nested problem like
// ["body", "tag_filter", "core"] is reported against the tag_filter field.
async function fieldErrors(response: Response): Promise<FieldError[]> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (!Array.isArray(body.detail)) return [];
    return body.detail.map((item: { loc?: unknown[]; msg?: string }) => {
      const loc = (item.loc ?? []).filter((part) => typeof part === "string" && part !== "body");
      return { field: String(loc[0] ?? "rule"), message: item.msg ?? "Invalid value" };
    });
  } catch {
    return [];
  }
}

async function ruleRequest(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await (init ? fetch(`${apiBaseUrl()}${path}`, init) : fetch(`${apiBaseUrl()}${path}`));
  } catch {
    throw new ApiError("Could not reach the ingestion API.", 0);
  }
  if (response.status === 422) {
    throw new RuleValidationError(await fieldErrors(response));
  }
  if (response.status === 404) {
    throw new ApiError("This rule no longer exists. It may have been deleted elsewhere.", 404);
  }
  if (!response.ok) {
    throw new ApiError(`Request failed with status ${response.status}`, response.status);
  }
  return response;
}

function jsonBody(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export async function fetchRules(): Promise<AlertRule[]> {
  const data = (await (await ruleRequest("/rules")).json()) as AlertRulesResponse;
  return data.rules;
}

export async function createRule(rule: AlertRuleInput): Promise<AlertRule> {
  return (await (await ruleRequest("/rules", jsonBody("POST", rule))).json()) as AlertRule;
}

export async function updateRule(id: number, rule: AlertRuleInput): Promise<AlertRule> {
  return (await (await ruleRequest(`/rules/${id}`, jsonBody("PUT", rule))).json()) as AlertRule;
}

export async function deleteRule(id: number): Promise<void> {
  await ruleRequest(`/rules/${id}`, { method: "DELETE" });
}
