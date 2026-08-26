import { useFacts, useInsiderActivity } from "../../hooks/useFacts";
import type { StructuredFact } from "../../types/models";

const LOOKBACK_DAYS = 90;

function usd(value: number): string {
  if (!value) return "-";
  const abs = Math.abs(value);
  if (abs >= 1e9) return `$${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(abs / 1e3).toFixed(0)}K`;
  return `$${abs.toFixed(0)}`;
}

function attr<T>(fact: StructuredFact, key: string): T | undefined {
  return fact.attributes?.[key] as T | undefined;
}

/** Filed insider transactions, kept visually distinct from the model-derived
 *  signals so the reader can tell which kind of evidence they are looking at:
 *  these are facts from an SEC filing, not a reading of one.
 *
 *  Open-market trades are separated from everything else throughout. Most Form 4
 *  activity is shares withheld to cover tax on vesting equity, or the disposal
 *  leg of an option exercise, both consequences of a compensation schedule
 *  rather than decisions to trade. One combined number is how this data gets
 *  misreported as "executives are dumping stock". */
export function ActivityPanel({ ticker }: { ticker: string }) {
  const { data: summary, isLoading } = useInsiderActivity(ticker, LOOKBACK_DAYS);
  const { data: facts } = useFacts(ticker, {
    fact_type: "insider_transaction",
    days: LOOKBACK_DAYS,
    limit: 60,
  });

  if (isLoading || !summary || summary.transactions === 0) return null;

  const openMarket = (facts ?? []).filter((f) => attr<boolean>(f, "is_open_market"));
  const routine = summary.transactions - summary.open_market_transactions;

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Insider activity</span>
        <span className="faint" style={{ fontSize: 9 }}>{LOOKBACK_DAYS}D · FORM 4</span>
      </div>

      <div className="stat-strip stat-strip-tight" style={{ border: "none", borderBottom: "1px solid var(--border)" }}>
        <div className="stat">
          <div className="stat-label">Sold</div>
          <div className="stat-value value-negative">
            {usd(summary.open_market_sold_usd)}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Bought</div>
          <div className="stat-value value-positive">
            {usd(summary.open_market_bought_usd)}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">People</div>
          <div className="stat-value">{summary.distinct_insiders}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Open / all</div>
          <div className="stat-value">
            {summary.open_market_transactions}<span className="faint">/{summary.transactions}</span>
          </div>
        </div>
      </div>

      <p className="faint" style={{ margin: 0, padding: "4px 8px", fontSize: 10, lineHeight: 1.45 }}>
        Figures count open-market trades only. The other {routine} filings are vesting,
        option exercises, and tax withholding.
      </p>

      {openMarket.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th className="num" style={{ width: 58 }}>Date</th>
              <th>Insider</th>
              <th className="num" style={{ width: 58 }}>Shares</th>
              <th className="num" style={{ width: 52 }}>Value</th>
            </tr>
          </thead>
          <tbody>
            {openMarket.slice(0, 10).map((fact) => {
              const disposed = attr<boolean>(fact, "disposed");
              return (
                <tr
                  key={fact.id}
                  className="clickable"
                  onClick={() => fact.source_url && window.open(fact.source_url, "_blank")}
                  title={attr<string>(fact, "officer_title") ?? undefined}
                >
                  <td className="num faint">
                    {new Date(fact.as_of_date).toLocaleDateString(undefined, { year: "2-digit", month: "2-digit", day: "2-digit" })}
                  </td>
                  <td className="prose mono" style={{ fontFamily: "var(--font-mono)" }}>
                    {attr<string>(fact, "owner") ?? "-"}
                  </td>
                  <td className={`num ${disposed ? "value-negative" : "value-positive"}`}>
                    {disposed ? "-" : "+"}
                    {Math.abs(Number(fact.value ?? 0)).toLocaleString()}
                  </td>
                  <td className="num dim">{usd(attr<number>(fact, "value_usd") ?? 0)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
