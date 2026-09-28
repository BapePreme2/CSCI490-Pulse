import type { TimeRangeOption } from "../metrics/timeRanges";

export function TimeRangeSelector({
  options,
  selected,
  onSelect,
}: {
  options: TimeRangeOption[];
  selected: TimeRangeOption;
  onSelect: (option: TimeRangeOption) => void;
}) {
  return (
    <div className="time-range-selector" role="group" aria-label="Time range">
      {options.map((option) => (
        <button
          key={option.label}
          type="button"
          className="time-range-button"
          aria-pressed={option.label === selected.label}
          onClick={() => onSelect(option)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
