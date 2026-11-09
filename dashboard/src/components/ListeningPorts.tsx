import type { LatestMetric } from "../api/types";
import { currentListeningPorts } from "../metrics/ports";

export function ListeningPorts({ metrics }: { metrics: LatestMetric[] }) {
  const ports = currentListeningPorts(metrics);

  return (
    <section className="chart-section">
      <div className="chart-section-header">
        <h2>Listening ports{ports && ports.length > 0 ? ` (${ports.length})` : ""}</h2>
      </div>
      {ports === null && <p className="page-status">No port scan reported yet.</p>}
      {ports !== null && ports.length === 0 && <p className="page-status">Nothing is listening on this host.</p>}
      {ports !== null && ports.length > 0 && (
        <table className="fleet-table">
          <thead>
            <tr>
              <th scope="col">Port</th>
              <th scope="col">Protocol</th>
              <th scope="col">Address</th>
              <th scope="col">Process</th>
            </tr>
          </thead>
          <tbody>
            {ports.map((p) => (
              <tr key={`${p.protocol}/${p.address}/${p.port}`}>
                <td>{p.port}</td>
                <td>{p.protocol.toUpperCase()}</td>
                <td>{p.address}</td>
                <td>{p.process ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
