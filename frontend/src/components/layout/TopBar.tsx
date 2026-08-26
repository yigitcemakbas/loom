import { useEffect, useState } from "react";
import { useIsFetching } from "@tanstack/react-query";
import { useApiHealth } from "../../hooks/useApiHealth";
import { useDashboard } from "../../hooks/useDashboard";

function clock(d: Date): string {
  return d.toLocaleTimeString(undefined, { hour12: false });
}

function signed(n: number): string {
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}`;
}

/** Status strip, carrying live portfolio figures rather than only a clock.
 *  A terminal's top line is prime real estate: it should tell you the state of
 *  what you are tracking without navigating anywhere. */
export function TopBar() {
  const [now, setNow] = useState(() => new Date());
  const { data: healthy } = useApiHealth();
  const fetching = useIsFetching();
  const { data } = useDashboard();

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const p = data?.portfolio;
  const sentiment = p?.avg_sentiment ?? null;

  return (
    <div className="topbar">
      <span className="topbar-item">
        <span
          className="live-dot"
          style={{
            background: healthy === false ? "var(--negative)" : "var(--positive)",
            opacity: fetching > 0 ? 0.45 : 1,
          }}
          title={healthy === false ? "API unreachable" : "API connected"}
        />
        <span className="faint">{fetching > 0 ? "SYNC" : "LIVE"}</span>
      </span>

      {p && (
        <>
          <span className="topbar-item">
            <span className="faint">COVERAGE</span>
            <span>{p.companies_covered}/{p.companies_total}</span>
          </span>
          <span className="topbar-item">
            <span className="faint">RISKS</span>
            <span className="value-negative">{p.total_risk_count}</span>
          </span>
          <span className="topbar-item">
            <span className="faint">SENTIMENT</span>
            <span className={sentiment === null ? "value-neutral" : sentiment >= 0 ? "value-positive" : "value-negative"}>
              {sentiment === null ? "-" : signed(sentiment)}
            </span>
          </span>
          <span className="topbar-item">
            <span className="faint">TREND</span>
            <span className="value-positive">{p.trend_up}&#9650;</span>
            <span className="value-negative">{p.trend_down}&#9660;</span>
          </span>
        </>
      )}

      <span className="topbar-item" style={{ marginLeft: "auto" }}>
        {now.toISOString().slice(0, 10)} {clock(now)} UTC{now.getTimezoneOffset() === 0 ? "" : ""}
      </span>
    </div>
  );
}
