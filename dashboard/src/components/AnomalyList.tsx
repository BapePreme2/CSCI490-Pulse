import type { AnomalySeries } from "../api/types";
import { anomalyNotes, formatDeviation, formatValue, formatWhen } from "../metrics/anomalies";
import { formatDuration } from "../rules/format";

export const MAX_LISTED_ANOMALIES = 8;

/** The anomalies marked on a history chart, as text, newest first: one
 * entry per event (a spike on every core is one entry), saying when, which
 * series, and the sample furthest from its baseline. */
export function AnomalyList({
  anomalies,
  sigma,
  windowSeconds,
  rangeSeconds,
  relevantKeys,
}: {
  anomalies: AnomalySeries[];
  sigma: number;
  windowSeconds: number;
  rangeSeconds: number;
  relevantKeys?: string[];
}) {
  const notes = anomalyNotes(anomalies, relevantKeys);
  const shown = notes.slice(0, MAX_LISTED_ANOMALIES);

  return (
    <div className="anomaly-notes">
      <p className="anomaly-notes-caption">
        Anomalies: samples {sigma}σ or more from their baseline, the {formatDuration(windowSeconds)} before
        each one.
      </p>
      {notes.length === 0 ? (
        <p className="anomaly-notes-empty">None in this range.</p>
      ) : (
        <ul className="anomaly-notes-list" aria-label="Anomalies">
          {shown.map((note) => {
            const { point, unit, series } = note.peak;
            const when =
              note.start === note.end
                ? formatWhen(note.start, rangeSeconds)
                : `${formatWhen(note.start, rangeSeconds)}–${formatWhen(note.end, rangeSeconds)}`;
            const direction = note.directions.length > 1 ? "above and below" : note.directions[0];
            const value = `${formatValue(point.value, unit)}, ${formatDeviation(point, unit)}`;
            return (
              <li key={note.key}>
                <span className="anomaly-notes-when">{when}</span>
                <span className="anomaly-notes-series">{note.series.join(", ")}</span>
                <span>
                  {direction} baseline ({note.samples} {note.samples === 1 ? "sample" : "samples"})
                  {note.samples === 1
                    ? `: ${value}`
                    : `, furthest off: ${note.series.length > 1 ? `${series} at ` : ""}${value}`}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {notes.length > shown.length && (
        <p className="anomaly-notes-more">
          Showing the {shown.length} most recent of {notes.length}.
        </p>
      )}
    </div>
  );
}
