import { Link } from "react-router-dom";
import { useUpcomingEarnings } from "../../hooks/useEarnings";
import type { EarningsOutlook } from "../../types/models";

// Beyond this the date is context rather than a call to action, and the strip
// stops earning its place at the top of the screen.
const SHOW_WITHIN_DAYS = 21;

function money(v: number | null): string | null {
  if (v === null) return null;
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString()}`;
}

function countdown(o: EarningsOutlook): string {
  if (o.days_until === null) return "-";
  if (o.days_until === 0) return "TODAY";
  if (o.days_until === 1) return "TOMORROW";
  return `${o.days_until}D`;
}

/** Upcoming earnings, soonest first.
 *
 *  Sits above everything else because an investor's decisions cluster around
 *  events: on the day a company reports, that fact outranks any standing view
 *  of it. The strip hides itself entirely when nothing is close, so it costs
 *  no attention on a quiet week. */
export function EarningsStrip() {
  const { data } = useUpcomingEarnings();

  const soon = (data ?? []).filter(
    (o) => o.days_until !== null && o.days_until <= SHOW_WITHIN_DAYS,
  );
  if (soon.length === 0) return null;

  return (
    <div className="panel" style={{ marginBottom: 8 }}>
      <div className="panel-head">
        <span className="panel-title">Reporting soon</span>
        <span className="faint" style={{ fontSize: 9 }}>NEXT {SHOW_WITHIN_DAYS} DAYS</span>
      </div>
      <table className="data-table">
        <tbody>
          {soon.map((o) => {
            const urgent = o.days_until !== null && o.days_until <= 1;
            const revenue = money(o.revenue_estimate);
            return (
              <tr key={o.ticker} className="clickable">
                <td style={{ width: 70 }}>
                  <span
                    className={urgent ? "tag tag-solid" : "tag tag-accent"}
                    style={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    {countdown(o)}
                  </span>
                </td>
                <td style={{ width: 60 }}>
                  <Link className="ticker-symbol" to={`/companies/${o.ticker}`}>{o.ticker}</Link>
                </td>
                <td className="faint" style={{ width: 76 }}>{o.quarter_label ?? ""}</td>
                <td className="faint" style={{ width: 130 }}>{o.when_label ?? ""}</td>
                <td className="num dim" style={{ width: 96 }}>
                  {o.eps_estimate !== null ? `${o.eps_estimate.toFixed(2)} EPS` : "-"}
                </td>
                <td className="num dim" style={{ width: 96 }}>{revenue ?? "-"}</td>
                {/* Track record is omitted rather than shown empty: a free
                    Finnhub key returns no historical actuals. */}
                <td className="faint">
                  {o.reports_seen >= 3
                    ? `Beat ${o.beats} of last ${o.reports_seen}`
                    : "expectations, consensus"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
