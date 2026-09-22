import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useFactorLeaderboard } from "../hooks/useFactors";
import { FACTOR_LABELS } from "../lib/factorLabels";

/** The whole universe, ranked on what the companies themselves reported.
 *
 *  Every other view in Loom covers the companies deep reading has reached,
 *  which is a handful, because reading costs model calls. This one covers
 *  everything, because its inputs are figures every filer is required to
 *  publish and the whole computation is arithmetic. It is the only page here
 *  that can answer "out of everything you track, where should I look".
 *
 *  Ranked within comparable companies rather than across all of them. A bank
 *  runs an enormous balance sheet by design and earns about one percent on it;
 *  measured against software companies every bank lands in the bottom decile
 *  on profitability and leverage, every time, and the result looks like a
 *  finding. */
export function FundamentalsPage() {
  const { data, isLoading } = useFactorLeaderboard();
  const [order, setOrder] = useState<"best" | "worst">("best");

  const rows = useMemo(() => {
    const all = data ?? [];
    return order === "best" ? all : [...all].reverse();
  }, [data, order]);

  if (isLoading) return <p className="empty-state">Scoring the universe…</p>;
  if (!data || data.length === 0) {
    return (
      <p className="empty-state">
        No factor scores yet. They are computed from filed financial statements,
        so this fills in once fundamentals have been ingested.
      </p>
    );
  }

  return (
    <div>
      <header className="today-head">
        <h1>What the numbers say</h1>
        <p className="today-sub">
          {data.length} companies scored on measures drawn from their own filed
          financial statements. No opinion, no reading, no interpretation: every
          score here is arithmetic over figures the company was required to
          publish, which is why this covers the whole universe rather than the
          few companies Loom has read in depth.
        </p>
      </header>

      <div className="horizon-row" style={{ marginBottom: 12 }}>
        <span className="horizon-hint">show</span>
        <button
          className={`horizon-btn ${order === "best" ? "active" : ""}`}
          onClick={() => setOrder("best")}
        >
          STRONGEST
        </button>
        <button
          className={`horizon-btn ${order === "worst" ? "active" : ""}`}
          onClick={() => setOrder("worst")}
        >
          WEAKEST
        </button>
      </div>

      <div className="panel">
        <table className="data-table">
          <thead>
            <tr>
              <th>Tkr</th>
              <th>Company</th>
              <th>Stands out on</th>
              <th className="num">Health</th>
              <th className="num" title="Average rank across themes. Measured as no better than chance; kept as a rough ordering only.">Rank</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.ticker}>
                <td>
                  <Link className="ticker-symbol" to={`/companies/${row.ticker}`}>
                    {row.ticker}
                  </Link>
                </td>
                <td className="dim prose-wrap">{row.name}</td>
                <td className="dim prose-wrap" style={{ fontSize: 10.5 }}>
                  {row.extremes.length === 0
                    ? <span className="faint">nothing unusual</span>
                    : row.extremes.map((k) => FACTOR_LABELS[k] ?? k).join(", ")}
                </td>
                {/* Deliberately quiet. Two independent backtests over 203
                    rebalances measured this number at t=-0.07 and t=-0.42,
                    which is indistinguishable from zero, so it is kept as a
                    rough sort key and stripped of the authority that a bold
                    figure in a leading column carries. The readings beside it
                    are the ones that held up. */}
                <td className="num dim" style={{ fontSize: 10 }}>
                  {row.composite.toFixed(2)}
                  <span className="faint" style={{ fontSize: 9 }}> /{row.factor_count}</span>
                </td>
                <td className="num dim">
                  {row.health_available
                    ? `${row.health_passed}/${row.health_available}`
                    : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="today-uncovered">
        "Stands out on" lists the measures where this company sits in the top or
        bottom tenth of comparable companies. Those are the readings worth your
        attention. Health is how many standard fundamental tests it passes, out of
        those Loom has the figures to run.
        <br /><br />
        Rank averages a company's standing across every theme. Loom backtested it
        over 203 rebalances and sixteen years and measured it as no better than
        chance, so it is shown only as a rough ordering and should carry no weight
        in a decision. The individual measures held up considerably better than
        their average did.
      </p>
    </div>
  );
}

