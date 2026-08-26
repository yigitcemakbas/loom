import { Link } from "react-router-dom";
import { Confidence } from "../shared/Confidence";
import { SentimentValue } from "../shared/SentimentValue";
import { Sparkline } from "../shared/Sparkline";
import { TrendArrow } from "../shared/TrendArrow";
import type { CompanyDashboardRow } from "../../types/models";

interface Props {
  rows: CompanyDashboardRow[];
  onClear: () => void;
}

/** Metrics-as-rows, companies-as-columns, the actual point of comparing is
 * reading one metric across several companies at once, which a table of
 * company-rows can't do. Everything here comes from data the Overview table
 * already fetched; no separate query. */
export function CompareTable({ rows, onClear }: Props) {
  return (
    <div className="panel" style={{ marginBottom: 8 }}>
      <div className="panel-head">
        <span className="panel-title">Compare ({rows.length})</span>
        <button className="link-button" onClick={onClear}>clear</button>
      </div>
      <div className="panel-body" style={{ overflowX: "auto" }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Metric</th>
              {rows.map((r) => (
                <th key={r.company_id}>
                  <Link to={`/companies/${r.ticker}`} className="ticker-symbol">{r.ticker}</Link>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Sentiment</td>
              {rows.map((r) => <td key={r.company_id} className="num"><SentimentValue value={r.sentiment_score} /></td>)}
            </tr>
            <tr>
              <td>Trend</td>
              {rows.map((r) => <td key={r.company_id} className="num"><TrendArrow value={r.sentiment_trend} /></td>)}
            </tr>
            <tr>
              <td>History</td>
              {rows.map((r) => <td key={r.company_id} className="num"><Sparkline points={r.sentiment_history} /></td>)}
            </tr>
            <tr>
              <td>Open risks (90d)</td>
              {rows.map((r) => (
                <td key={r.company_id} className="num">
                  <span className={r.risk_count > 0 ? "mono value-negative" : "mono value-neutral"}>{r.risk_count}</span>
                </td>
              ))}
            </tr>
            <tr>
              <td>Signals (90d)</td>
              {rows.map((r) => <td key={r.company_id} className="num mono">{r.signal_count}</td>)}
            </tr>
            <tr>
              <td>Top finding</td>
              {rows.map((r) => (
                <td key={r.company_id} style={{ maxWidth: 240 }}>
                  {r.top_signal ? (
                    <>
                      {r.top_signal.summary} <Confidence value={r.top_signal.confidence} />
                    </>
                  ) : (
                    <span className="value-neutral">none</span>
                  )}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
