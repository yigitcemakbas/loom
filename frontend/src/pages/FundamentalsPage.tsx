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
              <th className="num">Score</th>
              <th className="num">Health</th>
              <th>Stands out on</th>
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
                <td className="num">
                  <span className={scoreTone(row.composite)}>{row.composite.toFixed(2)}</span>
                  {/* The measure count travels with the score everywhere it is
                      shown. A 0.82 from four measures and a 0.82 from eleven
                      are different claims and look identical without it. */}
                  <span className="faint" style={{ fontSize: 9 }}> /{row.factor_count}</span>
                </td>
                <td className="num dim">
                  {row.health_available
                    ? `${row.health_passed}/${row.health_available}`
                    : "-"}
                </td>
                <td className="dim prose-wrap" style={{ fontSize: 10.5 }}>
                  {row.extremes.length === 0
                    ? <span className="faint">nothing unusual</span>
                    : row.extremes.map((k) => FACTOR_LABELS[k] ?? k).join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="today-uncovered">
        Score is the average of where a company ranks among comparable companies
        on each measure, from 0 (worst) to 1 (best). Health is how many standard
        fundamental tests it passes, out of those Loom has the figures to run.
        A high score is not a recommendation: these measures describe what has
        already been reported, and none of them knows what a share costs.
      </p>
    </div>
  );
}

function scoreTone(score: number): string {
  if (score >= 0.65) return "value-positive";
  if (score <= 0.35) return "value-negative";
  return "";
}
