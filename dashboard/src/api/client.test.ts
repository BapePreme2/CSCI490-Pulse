import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  HostNotFoundError,
  RuleValidationError,
  createRule,
  deleteRule,
  fetchAlerts,
  fetchHostSummary,
  fetchHosts,
  fetchLatestMetrics,
  fetchMetricHistory,
  fetchRules,
  updateRule,
} from "./client";
import type { AlertRuleInput } from "./types";

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockFetch(response: Partial<Response>) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}), ...response });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("fetchLatestMetrics", () => {
  it("returns the metrics array on success", async () => {
    mockFetch({
      json: async () => ({
        host: "web-1",
        metrics: [{ name: "cpu.usage", unit: "percent", tags: {}, value: 1, timestamp: 1 }],
      }),
    });

    const metrics = await fetchLatestMetrics("web-1");

    expect(metrics).toHaveLength(1);
    expect(metrics[0].name).toBe("cpu.usage");
  });

  it("requests the expected URL, escaping the hostname", async () => {
    const fetchMock = mockFetch({ json: async () => ({ host: "a/b", metrics: [] }) });

    await fetchLatestMetrics("a/b");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/hosts/a%2Fb/metrics/latest");
  });

  it("throws HostNotFoundError on a 404", async () => {
    mockFetch({ ok: false, status: 404 });

    await expect(fetchLatestMetrics("nope")).rejects.toThrow(HostNotFoundError);
  });

  it("throws ApiError on other non-2xx statuses", async () => {
    mockFetch({ ok: false, status: 503 });

    await expect(fetchLatestMetrics("web-1")).rejects.toThrow(ApiError);
  });

  it("throws ApiError when the network request itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));

    await expect(fetchLatestMetrics("web-1")).rejects.toThrow(ApiError);
  });
});

describe("fetchMetricHistory", () => {
  it("returns the response on success", async () => {
    mockFetch({
      json: async () => ({
        host: "web-1",
        name: "cpu.usage",
        start: 0,
        end: 100,
        series: [{ unit: "percent", tags: {}, points: [{ timestamp: 50, value: 12.5 }] }],
      }),
    });

    const history = await fetchMetricHistory("web-1", "cpu.usage");

    expect(history.series).toHaveLength(1);
    expect(history.series[0].points[0].value).toBe(12.5);
  });

  it("omits start/end from the query string when not given", async () => {
    const fetchMock = mockFetch({ json: async () => ({ host: "web-1", name: "cpu.usage", start: 0, end: 0, series: [] }) });

    await fetchMetricHistory("web-1", "cpu.usage");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/hosts/web-1/metrics/cpu.usage");
  });

  it("includes start/end and escapes the metric name when given", async () => {
    const fetchMock = mockFetch({ json: async () => ({ host: "web-1", name: "cpu.usage", start: 1, end: 2, series: [] }) });

    await fetchMetricHistory("web-1", "cpu/usage", 1, 2);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/hosts/web-1/metrics/cpu%2Fusage?start=1&end=2",
    );
  });

  it("throws HostNotFoundError on a 404", async () => {
    mockFetch({ ok: false, status: 404 });

    await expect(fetchMetricHistory("nope", "cpu.usage")).rejects.toThrow(HostNotFoundError);
  });

  it("throws ApiError on other non-2xx statuses", async () => {
    mockFetch({ ok: false, status: 400 });

    await expect(fetchMetricHistory("web-1", "cpu.usage")).rejects.toThrow(ApiError);
  });
});

describe("fetchHosts", () => {
  it("returns the hosts array on success", async () => {
    mockFetch({
      json: async () => ({
        hosts: [
          { hostname: "web-1", first_seen_at: 1, last_seen_at: 2, cpu_usage: 10, memory_percent: 50 },
        ],
      }),
    });

    const hosts = await fetchHosts();

    expect(hosts).toEqual([
      { hostname: "web-1", first_seen_at: 1, last_seen_at: 2, cpu_usage: 10, memory_percent: 50 },
    ]);
  });

  it("requests /hosts", async () => {
    const fetchMock = mockFetch({ json: async () => ({ hosts: [] }) });

    await fetchHosts();

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/hosts");
  });

  it("throws ApiError on a non-2xx status (no per-host 404 case exists here)", async () => {
    mockFetch({ ok: false, status: 503 });

    await expect(fetchHosts()).rejects.toThrow(ApiError);
  });

  it("throws ApiError when the network request itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));

    await expect(fetchHosts()).rejects.toThrow(ApiError);
  });
});

describe("fetchHostSummary", () => {
  it("returns the summary on success", async () => {
    mockFetch({
      json: async () => ({
        hostname: "web-1", first_seen_at: 1, last_seen_at: 2, status: "online", cpu_usage: 10, memory_percent: 50,
      }),
    });

    const summary = await fetchHostSummary("web-1");

    expect(summary.status).toBe("online");
  });

  it("requests /hosts/{host}, escaping the hostname", async () => {
    const fetchMock = mockFetch({
      json: async () => ({ hostname: "a/b", first_seen_at: 0, last_seen_at: 0, status: "online", cpu_usage: null, memory_percent: null }),
    });

    await fetchHostSummary("a/b");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/hosts/a%2Fb");
  });

  it("throws HostNotFoundError on a 404", async () => {
    mockFetch({ ok: false, status: 404 });

    await expect(fetchHostSummary("nope")).rejects.toThrow(HostNotFoundError);
  });

  it("throws ApiError on other non-2xx statuses", async () => {
    mockFetch({ ok: false, status: 503 });

    await expect(fetchHostSummary("web-1")).rejects.toThrow(ApiError);
  });
});

const RULE_INPUT: AlertRuleInput = {
  name: "High CPU",
  metric_name: "cpu.usage",
  operator: ">",
  threshold: 80,
  duration_seconds: 300,
  host: null,
  tag_filter: { core: null },
  enabled: true,
};

describe("alert rule requests", () => {
  it("fetchRules returns the rules array", async () => {
    const fetchMock = mockFetch({ json: async () => ({ rules: [{ ...RULE_INPUT, id: 1 }] }) });

    const rules = await fetchRules();

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/rules");
    expect(rules.map((r) => r.id)).toEqual([1]);
  });

  it("createRule POSTs the rule as JSON and returns the stored rule", async () => {
    const fetchMock = mockFetch({ status: 201, json: async () => ({ ...RULE_INPUT, id: 7 }) });

    const created = await createRule(RULE_INPUT);

    expect(created.id).toBe(7);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(RULE_INPUT),
    });
  });

  it("updateRule PUTs to the rule's own URL", async () => {
    const fetchMock = mockFetch({ json: async () => ({ ...RULE_INPUT, id: 7 }) });

    await updateRule(7, RULE_INPUT);

    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/rules/7");
    expect(fetchMock.mock.calls[0][1].method).toBe("PUT");
  });

  it("deleteRule sends DELETE and doesn't try to parse the empty 204 body", async () => {
    const json = vi.fn();
    const fetchMock = mockFetch({ status: 204, json });

    await deleteRule(7);

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/rules/7", { method: "DELETE" });
    expect(json).not.toHaveBeenCalled();
  });

  it("turns a 422 into a RuleValidationError listing each top-level field", async () => {
    mockFetch({
      ok: false,
      status: 422,
      json: async () => ({
        detail: [
          { loc: ["body", "threshold"], msg: "Input should be a finite number" },
          { loc: ["body", "tag_filter", "core"], msg: "bad tag" },
          { loc: ["body", "host"], msg: "Unknown host: ghost (it has never reported)" },
        ],
      }),
    });

    const error = await createRule(RULE_INPUT).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(RuleValidationError);
    expect((error as RuleValidationError).errors).toEqual([
      { field: "threshold", message: "Input should be a finite number" },
      { field: "tag_filter", message: "bad tag" },
      { field: "host", message: "Unknown host: ghost (it has never reported)" },
    ]);
  });

  it("still throws RuleValidationError when a 422 body is unreadable", async () => {
    mockFetch({ ok: false, status: 422, json: async () => { throw new SyntaxError("not json"); } });

    const error = await createRule(RULE_INPUT).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(RuleValidationError);
    expect((error as RuleValidationError).errors).toEqual([]);
  });

  it("explains a 404 as a rule that no longer exists", async () => {
    mockFetch({ ok: false, status: 404 });

    await expect(updateRule(9, RULE_INPUT)).rejects.toThrow("This rule no longer exists");
  });

  it("throws ApiError on other failures and when the API is unreachable", async () => {
    mockFetch({ ok: false, status: 503 });
    await expect(fetchRules()).rejects.toThrow(ApiError);

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));
    await expect(deleteRule(1)).rejects.toThrow("Could not reach the ingestion API.");
  });
});

describe("fetchAlerts", () => {
  it("returns the whole response, since the page needs has_more and firing_count too", async () => {
    const body = { alerts: [], has_more: true, firing_count: 2 };
    mockFetch({ json: async () => body });

    expect(await fetchAlerts()).toEqual(body);
  });

  it("requests /alerts with no query string when there are no filters", async () => {
    const fetchMock = mockFetch({ json: async () => ({ alerts: [], has_more: false, firing_count: 0 }) });

    await fetchAlerts();

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/alerts");
  });

  it("sends the filters and limit, escaping the host", async () => {
    const fetchMock = mockFetch({ json: async () => ({ alerts: [], has_more: false, firing_count: 0 }) });

    await fetchAlerts({ host: "a b&c", state: "firing", limit: 200 });

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/alerts?host=a+b%26c&state=firing&limit=200");
  });

  it("throws ApiError on a non-2xx status and when the API is unreachable", async () => {
    mockFetch({ ok: false, status: 503 });
    await expect(fetchAlerts()).rejects.toThrow(ApiError);

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(fetchAlerts()).rejects.toThrow("Could not reach the ingestion API.");
  });
});
