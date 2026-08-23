import { useState } from "react";

interface Props {
  onAdd: (ticker: string) => void;
  disabled: boolean;
  label: string;
}

/** Phase 1: a plain inline form. The plan's "AddTickerModal" richness
 * (search-by-name, matching UX) can come later without changing this
 * component's contract.
 *
 * `disabled`/`label` are driven entirely by the parent: whether the button
 * says "Add ticker", "Adding…", or "Loading…" (and whether it's clickable)
 * depends on state this component has no visibility into, so it stays
 * dumb rather than guessing. */
export function AddTickerForm({ onAdd, disabled, label }: Props) {
  const [ticker, setTicker] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!ticker.trim()) return;
    onAdd(ticker.trim().toUpperCase());
    setTicker("");
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
      <input
        type="text"
        placeholder="Ticker (e.g. AAPL)"
        value={ticker}
        onChange={(e) => setTicker(e.target.value)}
      />
      <button className="btn" type="submit" disabled={disabled}>
        {label}
      </button>
    </form>
  );
}
