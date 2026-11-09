import type { LatestMetric } from "../api/types";

export interface ListeningPort {
  protocol: string;
  address: string;
  port: number;
  process?: string;
  /** A sensitive port bound to an address other machines can reach, as
   * judged by the agent (SEC-02). */
  exposed: boolean;
}

// The agent stamps one cycle's port samples and its count with the same
// time, which survives storage exactly; the slack only absorbs float noise.
const SAME_CYCLE_TOLERANCE_SECONDS = 0.001;

/** The ports the host was listening on in its latest port scan, exposed
 * ones first and then by port, or null if the agent has never reported one.
 *
 * "Latest" values are per series, and a closed port's series keeps its last
 * sample forever, so a port counts as open only if it was reported in the
 * same cycle as the newest `network.listening_ports` count. The agent sends
 * that count every cycle, even when it's 0. */
export function currentListeningPorts(metrics: LatestMetric[]): ListeningPort[] | null {
  const counts = metrics.filter((m) => m.name === "network.listening_ports");
  if (counts.length === 0) return null;
  const scannedAt = Math.max(...counts.map((m) => m.timestamp));

  return metrics
    .filter(
      (m) =>
        m.name === "network.listening_port" && Math.abs(m.timestamp - scannedAt) < SAME_CYCLE_TOLERANCE_SECONDS,
    )
    .map((m) => ({
      protocol: m.tags.protocol ?? "?",
      address: m.tags.address ?? "?",
      port: Number(m.tags.port),
      ...(m.tags.process ? { process: m.tags.process } : {}),
      exposed: m.tags.exposed === "true",
    }))
    .sort(
      (a, b) =>
        Number(b.exposed) - Number(a.exposed) ||
        a.port - b.port ||
        a.protocol.localeCompare(b.protocol) ||
        a.address.localeCompare(b.address),
    );
}

/** "5432/tcp (postgres)"-style labels for the distinct exposed services, so
 * a service bound on both IPv4 and IPv6 is named once. */
export function exposedServiceLabels(ports: ListeningPort[]): string[] {
  const processByService = new Map<string, string | undefined>();
  for (const p of ports) {
    if (!p.exposed) continue;
    const service = `${p.port}/${p.protocol}`;
    // Prefer whichever bind's process name the agent could see.
    processByService.set(service, processByService.get(service) ?? p.process);
  }
  return [...processByService].map(([service, process]) => (process ? `${service} (${process})` : service));
}
