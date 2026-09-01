import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { SentimentPoint } from "../../types/models";

interface Props {
  points: SentimentPoint[];
}

// Comfortably inside the ~420px rail the chart sits in, with room for the
// chart's own margins so the box never reaches the window edge.
const TOOLTIP_MAX_WIDTH = 260;

// A summary is a full sentence written for the signal table, where it has the
// width to breathe. Past this it is truncated rather than allowed to grow a
// hover card tall enough to cover the chart it is describing.
const MAX_SUMMARY_CHARS = 180;

interface ToneTooltipProps {
  active?: boolean;
  payload?: { payload: { date: string; sentiment: number; summary?: string } }[];
}

/** Replaces recharts' default tooltip rather than restyling it.
 *
 *  The default sets `white-space: nowrap` inline, so a one-sentence summary
 *  renders as a single unbroken line that ignores `maxWidth` entirely and runs
 *  off both the panel and the window. Overriding that one property through
 *  `contentStyle` works, but every other constraint here (wrapping, the score
 *  and the prose being separate blocks, a width that respects the rail) is
 *  fighting the default markup for control it does not offer. Owning the
 *  markup is smaller than the workaround.
 */
function ToneTooltip({ active, payload }: ToneTooltipProps) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;

  const score = point.sentiment;
  const summary = point.summary;
  const truncated =
    summary && summary.length > MAX_SUMMARY_CHARS
      ? `${summary.slice(0, MAX_SUMMARY_CHARS).trimEnd()}...`
      : summary;

  return (
    <div
      style={{
        maxWidth: TOOLTIP_MAX_WIDTH,
        background: "var(--bg-inset)",
        border: "1px solid var(--border-strong)",
        padding: "6px 8px",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        lineHeight: 1.45,
        // The whole point: prose wraps instead of extending the box sideways.
        whiteSpace: "normal",
        overflowWrap: "anywhere",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <span style={{ color: "var(--text-dim)" }}>{point.date}</span>
        <span
          className="mono"
          style={{
            color:
              score > 0.05 ? "var(--positive)"
              : score < -0.05 ? "var(--negative)"
              : "var(--text-faint)",
          }}
        >
          {score > 0 ? "+" : ""}
          {score.toFixed(2)}
        </span>
      </div>
      {truncated && (
        <div style={{ marginTop: 4, color: "var(--text)" }}>{truncated}</div>
      )}
    </div>
  );
}

/** Management tone over time. Each point is one analysed filing, so the line
 *  reads as "how did tone move filing to filing", not a continuous series. */
export function SentimentTrendChart({ points }: Props) {
  if (points.length < 2) {
    return (
      <div className="empty-state">
        Not enough analysed filings yet to plot a trend. At least two are needed.
      </div>
    );
  }

  const data = points.map((p) => ({
    date: new Date(p.occurred_at).toLocaleDateString(undefined, { year: "2-digit", month: "short" }),
    sentiment: Number(p.sentiment_score.toFixed(2)),
    summary: p.summary,
  }));

  return (
    <ResponsiveContainer width="100%" height={190}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: -20 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="2 2" />
        <XAxis dataKey="date" stroke="var(--text-faint)" fontSize={10} tickLine={false} />
        <YAxis domain={[-1, 1]} ticks={[-1, -0.5, 0, 0.5, 1]} stroke="var(--text-faint)" fontSize={10} tickLine={false} />
        <ReferenceLine y={0} stroke="var(--text-faint)" strokeDasharray="1 3" />
        <Tooltip
          // Kept inside the chart's own box. The chart lives in a narrow right
          // rail pinned to the window edge, so a tooltip allowed to escape has
          // nowhere to go but off the screen.
          allowEscapeViewBox={{ x: false, y: false }}
          wrapperStyle={{ zIndex: 5, outline: "none" }}
          content={<ToneTooltip />}
        />
        <Line
          type="monotone" dataKey="sentiment" stroke="var(--accent)" strokeWidth={1.5}
          dot={{ r: 2.5, fill: "var(--accent)" }} activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
