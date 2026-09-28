import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, HostNotFoundError, fetchLatestMetrics } from "./client";

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
