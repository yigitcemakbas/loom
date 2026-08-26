import type { ReactNode } from "react";
import { SentimentValue } from "../shared/SentimentValue";
import type { PortfolioSummary } from "../../types/models";

interface Props {
  portfolio: PortfolioSummary;
}

/** A real fold of the same per-company rollups the table below shows, not
 * a separate query, not decoration. This is the "portfolio" read: what does
 * the whole watchlist look like right now, before drilling into any one row. */
export function PortfolioSummaryStrip({ portfolio: p }: Props) {
  return (
    <div className="stat-strip" style={{ marginBottom: 8 }}>
      <Stat label="Coverage" value={`${p.companies_covered}/${p.companies_total}`} sub="analyzed" />
      <Stat
        label="Open risks"
        value={String(p.total_risk_count)}
        sub="90d"
        valueClass={p.total_risk_count > 0 ? "value-negative" : "value-neutral"}
      />
      <Stat
        label="Portfolio sentiment"
        value={<SentimentValue value={p.avg_sentiment} />}
        sub={`avg of ${p.companies_covered}`}
      />
      <Stat
        label="Trend"
        value={
          <span className="mono">
            <span className="value-positive">{p.trend_up}▲</span> <span className="value-negative">{p.trend_down}▼</span>
          </span>
        }
        sub="companies"
      />
      <Stat
        label="Most active"
        value={p.most_active_ticker ?? "-"}
        sub={p.most_active_ticker ? `${p.most_active_signal_count} signals` : undefined}
      />
    </div>
  );
}

function Stat({
  label, value, sub, valueClass,
}: { label: string; value: ReactNode; sub?: string; valueClass?: string }) {
  return (
    <div className="stat">
      <div
        className="mono"
        style={{ fontSize: 10, color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.06em" }}
      >
        {label}
      </div>
      <div className={`stat-value ${valueClass ?? ""}`}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}
