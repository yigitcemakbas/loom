import { useMemo, useState } from "react";
import type { PriceSeries } from "../../types/models";

interface Props {
  series: PriceSeries;
  height?: number;
}

/** Inline SVG rather than a charting library.
 *
 *  This panel re-renders every five seconds as the rotation advances, so the
 *  mount cost matters: a chart instance per tick would be real overhead for a
 *  single line. It also keeps the terminal look, no library defaults to fight,
 *  and adds no dependency for something this simple. */
export function PriceChart({ series, height = 132 }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 320;
  const padY = 8;

  const geometry = useMemo(() => {
    const values = series.points.map((p) => p.c);
    if (values.length < 2) return null;

    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = width / (values.length - 1);

    const coords = values.map((v, i) => ({
      x: i * stepX,
      y: padY + (1 - (v - min) / span) * (height - padY * 2),
    }));

    const line = coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(2)},${c.y.toFixed(2)}`).join(" ");
    const area = `${line} L${width},${height} L0,${height} Z`;

    // The open of the window, so the fill shows ground gained or lost across
    // exactly the period the line covers.
    const baseline = padY + (1 - (values[0] - min) / span) * (height - padY * 2);

    return { coords, line, area, min, max, baseline };
  }, [series.points, height]);

  if (!geometry) {
    return <div className="empty-state" style={{ height }}>Not enough price data.</div>;
  }

  const up = (series.change ?? 0) >= 0;
  const stroke = up ? "var(--positive)" : "var(--negative)";
  const point = hover !== null ? series.points[hover] : null;
  const marker = hover !== null ? geometry.coords[hover] : null;

  return (
    <div style={{ position: "relative" }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        style={{ width: "100%", height, display: "block" }}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const ratio = (e.clientX - box.left) / box.width;
          const index = Math.round(ratio * (series.points.length - 1));
          setHover(Math.max(0, Math.min(series.points.length - 1, index)));
        }}
      >
        <defs>
          <linearGradient id={`fill-${series.ticker}-${series.range}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Where the window opened: the line above it is gain, below is loss. */}
        <line
          x1="0" y1={geometry.baseline} x2={width} y2={geometry.baseline}
          stroke="var(--border-strong)" strokeWidth="1" strokeDasharray="2 3"
          vectorEffect="non-scaling-stroke"
        />
        <path d={geometry.area} fill={`url(#fill-${series.ticker}-${series.range})`} />
        <path d={geometry.line} fill="none" stroke={stroke} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />

        {marker && (
          <>
            <line
              x1={marker.x} y1="0" x2={marker.x} y2={height}
              stroke="var(--text-faint)" strokeWidth="1" vectorEffect="non-scaling-stroke"
            />
            <circle cx={marker.x} cy={marker.y} r="2.5" fill={stroke} vectorEffect="non-scaling-stroke" />
          </>
        )}
      </svg>

      <div
        className="mono"
        style={{
          display: "flex", justifyContent: "space-between",
          fontSize: 9, color: "var(--text-faint)", padding: "2px 2px 0",
        }}
      >
        <span>{geometry.min.toFixed(2)}</span>
        {point && (
          <span style={{ color: "var(--text-dim)" }}>
            {new Date(point.t * 1000).toLocaleString(undefined, {
              month: "short", day: "numeric",
              hour: series.range === "1H" || series.range === "24H" ? "2-digit" : undefined,
              minute: series.range === "1H" || series.range === "24H" ? "2-digit" : undefined,
            })}
            {" · "}
            {point.c.toFixed(2)}
          </span>
        )}
        <span>{geometry.max.toFixed(2)}</span>
      </div>
    </div>
  );
}
