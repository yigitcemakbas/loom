import { useState } from "react";
import { PriceChart } from "./PriceChart";
import { usePrices } from "../../hooks/usePrices";

const RANGES = ["1H", "24H", "1W", "1M", "6M", "1Y"];

/** One company's price, with the same timescales as the rotating panel.
 *
 *  Not a rotation: on a company page the reader has already chosen the
 *  company, so cycling away from it would be actively unhelpful. */
export function CompanyPricePanel({ ticker }: { ticker: string }) {
  const [range, setRange] = useState("24H");
  const { data, isLoading, isError } = usePrices(ticker, range);

  if (isError) return null;

  const up = (data?.change ?? 0) >= 0;

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Price</span>
        {data?.last != null && (
          <span className={`mono ${up ? "value-positive" : "value-negative"}`} style={{ fontSize: 10 }}>
            {data.last.toFixed(2)}{" "}
            {data.change_percent != null && `(${data.change_percent >= 0 ? "+" : ""}${data.change_percent.toFixed(2)}%)`}
          </span>
        )}
      </div>

      <div style={{ padding: "4px 8px 0" }}>
        {isLoading && !data ? (
          <div className="empty-state" style={{ height: 132 }}>Loading price…</div>
        ) : data ? (
          <PriceChart series={data} />
        ) : (
          <div className="empty-state" style={{ height: 132 }}>No price data.</div>
        )}
      </div>

      <div style={{ display: "flex", gap: 3, padding: "4px 8px 6px", flexWrap: "wrap" }}>
        {RANGES.map((r) => (
          <button
            key={r}
            className="btn"
            style={{
              padding: "1px 6px", height: 18, fontSize: 9,
              ...(r === range ? { background: "var(--accent)", color: "#14100a" } : {}),
            }}
            onClick={() => setRange(r)}
          >
            {r}
          </button>
        ))}
      </div>
    </div>
  );
}
