import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  HostNotFoundError,
  fetchHostSummary,
  fetchHosts,
  fetchLatestMetrics,
  fetchMetricHistory,
} from "./client";

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
