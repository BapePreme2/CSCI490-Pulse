import type { LatestMetric } from "../api/types";
import { currentListeningPorts, exposedServiceLabels } from "../metrics/ports";

export function ListeningPorts({ metrics }: { metrics: LatestMetric[] }) {
  const ports = currentListeningPorts(metrics);
  const exposed = ports ? exposedServiceLabels(ports) : [];

  return (
    <section className="chart-section">
      <div className="chart-section-header">
        <h2>Listening ports{ports && ports.length > 0 ? ` (${ports.length})` : ""}</h2>
      </div>
      {ports === null && <p className="page-status">No port scan reported yet.</p>}
      {ports !== null && ports.length === 0 && <p className="page-status">Nothing is listening on this host.</p>}
      {exposed.length > 0 && (
        <p role="alert" className="page-status page-status--warning">
          {exposed.length === 1 ? "1 sensitive service is" : `${exposed.length} sensitive services are`} listening on
          an address other machines can reach: {exposed.join(", ")}. A firewall may still block access; Pulse only
          sees what the host binds to.
        </p>
      )}
      {ports !== null && ports.length > 0 && (
        <table className="fleet-table">
          <thead>
            <tr>
              <th scope="col">Port</th>
              <th scope="col">Protocol</th>
              <th scope="col">Address</th>
              <th scope="col">Process</th>
              <th scope="col">Exposure</th>
            </tr>
          </thead>
          <tbody>
            {ports.map((p) => (
              <tr key={`${p.protocol}/${p.address}/${p.port}`} className={p.exposed ? "port-row--exposed" : undefined}>
                <td>{p.port}</td>
                <td>{p.protocol.toUpperCase()}</td>
                <td>{p.address}</td>
                <td>{p.process ?? "—"}</td>
                <td>{p.exposed && <span className="port-flag">Exposed</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
