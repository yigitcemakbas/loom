import type { SignalType } from "../../types/models";

const TYPES: { value: SignalType | ""; label: string }[] = [
  { value: "", label: "all types" },
  { value: "emerging_pattern", label: "emerging pattern" },
  { value: "qoq_anomaly", label: "new vs prior year" },
  { value: "new_risk_factor", label: "risk" },
  { value: "notable_quote", label: "quote" },
  { value: "sentiment_shift", label: "sentiment" },
  { value: "guidance_change", label: "guidance" },
  { value: "insider_activity", label: "insider activity" },
  { value: "short_interest_spike", label: "short interest" },
];

interface Props {
  signalType: SignalType | "";
  onSignalType: (value: SignalType | "") => void;
  minConfidence: number;
  onMinConfidence: (value: number) => void;
  unreviewedOnly: boolean;
  onUnreviewedOnly: (value: boolean) => void;
}

export function SignalFilterBar({
  signalType, onSignalType, minConfidence, onMinConfidence, unreviewedOnly, onUnreviewedOnly,
}: Props) {
  return (
    <div className="filter-bar">
      <select value={signalType} onChange={(e) => onSignalType(e.target.value as SignalType | "")}>
        {TYPES.map((t) => (
          <option key={t.value} value={t.value}>{t.label}</option>
        ))}
      </select>
      <label className="filter-label">
        min confidence <span className="mono">{Math.round(minConfidence * 100)}%</span>
        <input
          type="range" min={0} max={0.95} step={0.05}
          value={minConfidence}
          onChange={(e) => onMinConfidence(Number(e.target.value))}
        />
      </label>
      <label className="filter-label">
        <input type="checkbox" checked={unreviewedOnly} onChange={(e) => onUnreviewedOnly(e.target.checked)} />
        unreviewed only
      </label>
    </div>
  );
}
