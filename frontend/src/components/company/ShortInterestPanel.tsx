import { useFacts } from "../../hooks/useFacts";
import type { StructuredFact } from "../../types/models";

// FINRA publishes twice a month, so two years is roughly 48 readings. The
// window is deliberately long: a raw short position means nothing on its own,
// and the only cheap way to say whether 286 million shares is a lot is to
// compare it against the same company's own recent history.
const LOOKBACK_DAYS = 730;
const MAX_READINGS = 60;

// Above this, covering would take more than a trading week of normal volume.
// The conventional line for a crowded short, where an unwind starts to move
// the price on its own.
const CROWDED_DAYS_TO_COVER = 5;

function shares(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(abs / 1e3).toFixed(0)}K`;
  return abs.toFixed(0);
}

function attr<T>(fact: StructuredFact, key: string): T | undefined {
  const raw = fact.attributes?.[key];
  return raw === null ? undefined : (raw as T | undefined);
}

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** The chart spans two years, so its endpoints carry the year. Without it the
 *  axis reads "19 Sep ... 14 Aug", which looks like it runs backwards. */
function axisDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

function daysSince(iso: string): number {
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
}

/** Where the latest reading sits in its own history, 0 at the two-year low and
 *  1 at the high. This is the number that turns a share count into a read. */
function percentile(values: number[], latest: number): number | null {
  if (values.length < 8) return null;
  const below = values.filter((v) => v < latest).length;
  return below / (values.length - 1);
}

function ordinal(fraction: number): string {
  const pct = Math.round(fraction * 100);
  if (pct >= 98) return "2Y high";
  if (pct <= 2) return "2Y low";
  const suffix = pct % 10 === 1 && pct !== 11 ? "st"
    : pct % 10 === 2 && pct !== 12 ? "nd"
    : pct % 10 === 3 && pct !== 13 ? "rd" : "th";
  return `${pct}${suffix} pctile`;
}

/** A plain min/max sparkline. The shared Sparkline is built for sentiment: it
 *  pins a zero baseline and floors the range at ±0.05, which flattens a series
 *  measured in hundreds of millions of shares into a straight line. */
function ShortTrend({ points, width = 236, height = 40 }: { points: number[]; width?: number; height?: number }) {
  if (points.length < 2) return null;

  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const stepX = width / (points.length - 1);
  const y = (v: number) => height - ((v - min) / range) * (height - 4) - 2;
  const coords = points.map((v, i) => `${(i * stepX).toFixed(1)},${y(v).toFixed(1)}`);

  const rising = points[points.length - 1] > points[0];
  const color = rising ? "var(--negative)" : "var(--positive)";

  return (
    <svg width={width} height={height} style={{ display: "block" }} aria-hidden="true">
      <polyline
        points={`0,${height} ${coords.join(" ")} ${width},${height}`}
        fill={rising ? "var(--negative-dim)" : "var(--positive-dim)"}
        opacity={0.18}
        stroke="none"
      />
      <polyline points={coords.join(" ")} fill="none" stroke={color} strokeWidth={1.25} />
    </svg>
  );
}

/** Consolidated short interest as reported to FINRA.
 *
 *  Everything here is a filed figure, not a reading of one, which is why it
 *  sits beside the insider panel rather than among the signals.
 *
 *  Two honesty details are load-bearing. The data settles twice a month and
 *  publishes about eight days later, so the headline is always a fortnight or
 *  so stale and the panel says by how much rather than implying a live number.
 *  And a rising short position is coloured as bearish positioning while the
 *  footnote notes the same crowding is what fuels a squeeze, because stating
 *  only one half of that is how this figure gets misread. */
export function ShortInterestPanel({ ticker }: { ticker: string }) {
  const { data: facts, isLoading } = useFacts(ticker, {
    fact_type: "short_interest",
    days: LOOKBACK_DAYS,
    limit: MAX_READINGS,
  });

  if (isLoading || !facts || facts.length === 0) return null;

  // The route returns most recent first; the chart reads left to right.
  const ordered = [...facts].reverse();
  const series = ordered.map((f) => Number(f.value ?? 0)).filter((v) => v > 0);
  const latest = facts[0];
  const current = Number(latest.value ?? 0);
  if (!current) return null;

  const changePercent = attr<number>(latest, "change_percent");
  const daysToCover = attr<number>(latest, "days_to_cover");
  const rank = percentile(series, current);
  const lag = daysSince(latest.as_of_date);
  const crowded = daysToCover !== undefined && daysToCover >= CROWDED_DAYS_TO_COVER;

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Short interest</span>
        <span className="faint" style={{ fontSize: 9 }}>2Y · FINRA</span>
      </div>

      <div
        className="stat-strip stat-strip-tight"
        style={{ border: "none", borderBottom: "1px solid var(--border)" }}
      >
        <div className="stat">
          <div className="stat-label">Shares short</div>
          <div className="stat-value">{shares(current)}</div>
          {rank !== null && <div className="stat-sub">{ordinal(rank)}</div>}
        </div>
        <div className="stat">
          <div className="stat-label">Change</div>
          <div
            className={`stat-value ${
              changePercent === undefined ? ""
              : changePercent > 0 ? "value-negative"
              : changePercent < 0 ? "value-positive" : ""
            }`}
          >
            {changePercent === undefined
              ? "-"
              : `${changePercent > 0 ? "+" : ""}${changePercent.toFixed(1)}%`}
          </div>
          <div className="stat-sub">vs prior</div>
        </div>
        <div className="stat">
          <div className="stat-label">Days to cover</div>
          <div className={`stat-value ${crowded ? "value-negative" : ""}`}>
            {daysToCover === undefined ? "-" : daysToCover.toFixed(1)}
          </div>
          {daysToCover !== undefined && (
            <div className="stat-sub">{crowded ? "crowded" : "comfortable"}</div>
          )}
        </div>
      </div>

      {series.length >= 2 && (
        <div style={{ padding: "8px 8px 4px" }}>
          <ShortTrend points={series} />
          <div
            className="faint"
            style={{ display: "flex", justifyContent: "space-between", fontSize: 9, marginTop: 2 }}
          >
            <span>{axisDate(ordered[0].as_of_date)}</span>
            <span>{axisDate(latest.as_of_date)}</span>
          </div>
        </div>
      )}

      <p className="faint" style={{ margin: 0, padding: "4px 8px 6px", fontSize: 10, lineHeight: 1.45 }}>
        Settled {shortDate(latest.as_of_date)}, {lag} days ago. FINRA collects twice a month and
        publishes on a lag, so this is never live. Days to cover is the short position divided by
        average daily volume: a high figure means bears are committed, and also that they would
        have to buy to get out.
      </p>

      <table className="data-table">
        <thead>
          <tr>
            <th className="num" style={{ width: 58 }}>Settled</th>
            <th className="num">Short</th>
            <th className="num" style={{ width: 56 }}>Change</th>
            <th className="num" style={{ width: 44 }}>DTC</th>
          </tr>
        </thead>
        <tbody>
          {facts.slice(0, 8).map((fact) => {
            const change = attr<number>(fact, "change_percent");
            const dtc = attr<number>(fact, "days_to_cover");
            return (
              <tr key={fact.id}>
                <td className="num faint">
                  {new Date(fact.as_of_date).toLocaleDateString(undefined, {
                    year: "2-digit", month: "2-digit", day: "2-digit",
                  })}
                </td>
                <td className="num mono">{shares(Number(fact.value ?? 0))}</td>
                <td
                  className={`num ${
                    change === undefined ? "dim"
                    : change > 0 ? "value-negative"
                    : change < 0 ? "value-positive" : "dim"
                  }`}
                >
                  {change === undefined ? "-" : `${change > 0 ? "+" : ""}${change.toFixed(1)}%`}
                </td>
                <td className="num dim">{dtc === undefined ? "-" : dtc.toFixed(1)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
