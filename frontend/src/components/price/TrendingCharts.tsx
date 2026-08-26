import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PriceChart } from "./PriceChart";
import { usePrefetchPrices, usePrices } from "../../hooks/usePrices";

const ROTATE_MS = 5000;
const RANGES = ["1H", "24H", "1W", "1M", "6M", "1Y"];

interface Props {
  tickers: string[];
}

/** Price charts that cycle through the watchlist, five seconds each.
 *
 *  Behaviours that matter more than they look:
 *
 *  - Rotation pauses on hover and on any manual interaction. A panel that
 *    slides away mid-read is worse than no panel, and a reader who has just
 *    pressed "next" has said what they want to look at.
 *  - The chosen timescale persists across rotation. Picking 1W and then
 *    watching every ticker snap back to 24H would make the control useless.
 *  - The next ticker is prefetched, so the incoming slide carries a chart
 *    rather than a loading message. An animation that reveals a spinner is
 *    worse than no animation.
 *  - A ticker with no price series (an ETF, a delisting) is skipped rather
 *    than shown as an empty frame. */
export function TrendingCharts({ tickers }: Props) {
  const [index, setIndex] = useState(0);
  const [range, setRange] = useState("24H");
  const [paused, setPaused] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  // Which way the last move went, so the slide agrees with the control that
  // caused it rather than always travelling the same direction.
  const [direction, setDirection] = useState<1 | -1>(1);

  const live = tickers.filter((t) => !skipped.has(t));
  const position = live.length ? index % live.length : 0;
  const ticker = live.length ? live[position] : undefined;
  const nextTicker = live.length > 1 ? live[(position + 1) % live.length] : undefined;

  const { data, isLoading, isError } = usePrices(ticker, range);
  usePrefetchPrices(nextTicker, range);

  // A ticker the provider has no series for is dropped from the rotation
  // rather than retried on every pass.
  useEffect(() => {
    if (isError && ticker) setSkipped((prev) => new Set(prev).add(ticker));
  }, [isError, ticker]);

  const advance = useCallback((step: 1 | -1) => {
    setDirection(step);
    setIndex((i) => {
      const n = live.length || 1;
      return (i + step + n) % n;
    });
  }, [live.length]);

  // Kept in a ref so manual navigation can restart the clock without the
  // effect re-subscribing on every render.
  const advanceRef = useRef(advance);
  advanceRef.current = advance;

  useEffect(() => {
    if (paused || hovering || live.length < 2) return;
    const id = setInterval(() => advanceRef.current(1), ROTATE_MS);
    return () => clearInterval(id);
  }, [paused, hovering, live.length, index]);

  if (live.length === 0) return null;

  const up = (data?.change ?? 0) >= 0;
  const slideClass = direction === 1 ? "chart-slide-next" : "chart-slide-prev";

  return (
    <div
      className="panel"
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
    >
      <div className="panel-head">
        <span className="panel-title">Price</span>
        <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <button className="link-button" title="Previous" onClick={() => { setPaused(true); advance(-1); }}>‹</button>
          <span className="faint" style={{ fontSize: 9, minWidth: 30, textAlign: "center" }}>
            {position + 1}/{live.length}
          </span>
          <button className="link-button" title="Next" onClick={() => { setPaused(true); advance(1); }}>›</button>
          <button
            className="link-button"
            title={paused ? "Resume rotation" : "Pause rotation"}
            onClick={() => setPaused((p) => !p)}
            style={{ color: paused ? "var(--text-faint)" : "var(--accent)" }}
          >
            {paused ? "▶" : "❚❚"}
          </button>
        </span>
      </div>

      {/* Keyed on the ticker so React remounts this subtree on every rotation,
          which is what re-triggers the entrance animation. Range changes reuse
          the same key deliberately: switching timescale is not a rotation and
          should not slide. */}
      <div className="chart-viewport">
        <div key={ticker} className={slideClass}>
          <div style={{ padding: "6px 8px 0", display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <Link className="ticker-symbol" style={{ fontSize: 15 }} to={`/companies/${ticker}`}>{ticker}</Link>
            {data?.last != null && (
              <>
                <span className="mono chart-value" style={{ fontSize: 15 }}>{data.last.toFixed(2)}</span>
                <span className={`mono chart-value ${up ? "value-positive" : "value-negative"}`} style={{ fontSize: 11 }}>
                  {up ? "▲" : "▼"} {data.change != null ? Math.abs(data.change).toFixed(2) : "-"}
                  {data.change_percent != null && ` (${data.change_percent >= 0 ? "+" : ""}${data.change_percent.toFixed(2)}%)`}
                </span>
              </>
            )}
            <span className="faint" style={{ fontSize: 9, marginLeft: "auto" }}>{range}</span>
          </div>

          <div style={{ padding: "0 8px" }}>
            {isLoading && !data ? (
              <div className="empty-state" style={{ height: 132 }}>Loading price…</div>
            ) : data ? (
              <PriceChart series={data} />
            ) : (
              <div className="empty-state" style={{ height: 132 }}>No price data.</div>
            )}
          </div>
        </div>
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

      {(paused || hovering) && (
        <div className="faint" style={{ fontSize: 9, padding: "0 8px 5px" }}>
          {paused ? "Rotation paused" : "Rotation held while hovering"}
        </div>
      )}
    </div>
  );
}
