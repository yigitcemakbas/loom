import { useState } from "react";
import { SignalTable } from "../components/signals/SignalTable";
import { useSignals } from "../hooks/useSignals";

type Scope = "both" | "new_risk_factor" | "qoq_anomaly";

/** The engine's most differentiated output, risk findings verifiable
 * against a prior filing (qoq_anomaly) or newly extracted from the current
 * one (new_risk_factor), gets its own surface instead of sitting in the
 * flat Signal Feed next to lower-weight notable-quote findings. */
export function RiskTrackerPage() {
  const [scope, setScope] = useState<Scope>("both");
  const [ticker, setTicker] = useState("");

  const newRisks = useSignals({
    signal_type: "new_risk_factor",
    ticker: ticker || undefined,
    limit: 200,
  });
  const yoyRisks = useSignals({
    signal_type: "qoq_anomaly",
    ticker: ticker || undefined,
    limit: 200,
  });

  const isLoading = newRisks.isLoading || yoyRisks.isLoading;
  const isError = newRisks.isError || yoyRisks.isError;

  const merged = [
    ...(scope !== "qoq_anomaly" ? newRisks.data ?? [] : []),
    ...(scope !== "new_risk_factor" ? yoyRisks.data ?? [] : []),
  ].sort((a, b) => b.priority - a.priority);

  return (
    <div>
      <div className="page-title-row">
        <h2>Risk tracker</h2>
        <span className="faint" style={{ fontSize: 10 }}>{merged.length} FINDINGS</span>
      </div>

      <div className="filter-bar">
        <select value={scope} onChange={(e) => setScope(e.target.value as Scope)}>
          <option value="both">all risk findings</option>
          <option value="qoq_anomaly">new vs prior year only</option>
          <option value="new_risk_factor">newly extracted only</option>
        </select>
        <input
          type="text"
          placeholder="filter by ticker"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          style={{ width: 110 }}
        />
      </div>

      {isError && <p className="error-text">Can't reach the Loom API. Confirm the backend is running and reload.</p>}

      <div className="panel">
        {isLoading ? (
          <p className="empty-state">Loading…</p>
        ) : (
          <SignalTable signals={merged} emptyMessage="No risk findings match this filter." />
        )}
      </div>
    </div>
  );
}
