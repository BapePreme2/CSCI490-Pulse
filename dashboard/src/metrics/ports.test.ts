import { describe, expect, it } from "vitest";
import type { LatestMetric } from "../api/types";
import { currentListeningPorts } from "./ports";

function port(portNumber: number, timestamp: number, tags: Record<string, string> = {}): LatestMetric {
  return {
    name: "network.listening_port",
    unit: "bool",
    tags: { protocol: "tcp", address: "0.0.0.0", port: String(portNumber), ...tags },
    value: 1,
    timestamp,
  };
}

function count(value: number, timestamp: number): LatestMetric {
  return { name: "network.listening_ports", unit: "count", tags: {}, value, timestamp };
}

describe("currentListeningPorts", () => {
  it("is null when the agent has never reported a port scan", () => {
    expect(currentListeningPorts([port(22, 100)])).toBeNull();
  });

  it("keeps only ports from the latest scan, sorted by port", () => {
    const ports = currentListeningPorts([port(8080, 100), port(443, 100), port(22, 50), count(2, 100)]);

    expect(ports?.map((p) => p.port)).toEqual([443, 8080]);
  });

  it("is empty when the latest scan found nothing, even if old ports linger", () => {
    expect(currentListeningPorts([port(22, 50), count(0, 100)])).toEqual([]);
  });

  it("carries protocol, address, and process through", () => {
    const ports = currentListeningPorts([port(53, 100, { protocol: "udp", address: "::", process: "dnsmasq" }), count(1, 100)]);

    expect(ports).toEqual([{ protocol: "udp", address: "::", port: 53, process: "dnsmasq" }]);
  });

  it("uses the newest count when the agent's tags changed and left two count series", () => {
    const ports = currentListeningPorts([port(22, 50), port(80, 100), count(1, 50), count(1, 100)]);

    expect(ports?.map((p) => p.port)).toEqual([80]);
  });
});
