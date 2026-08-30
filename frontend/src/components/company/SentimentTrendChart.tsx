import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { SentimentPoint } from "../../types/models";

interface Props {
  points: SentimentPoint[];
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
          contentStyle={{
            background: "var(--bg-inset)",
            border: "1px solid var(--border-strong)",
            borderRadius: 0,
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            maxWidth: 320,
          }}
          labelStyle={{ color: "var(--text-dim)" }}
          // recharts 3 types `value` as ValueType | undefined and hands the
          // datum through `item`, so both are narrowed here rather than
          // asserted. The tooltip has to survive a null point in the series.
          formatter={(value, _name, item) => {
            const score = typeof value === "number" ? value : Number(value);
            const summary = (item?.payload as { summary?: string } | undefined)?.summary;
            if (!Number.isFinite(score)) return ["-", "tone"];
            return [
              `${score > 0 ? "+" : ""}${score}${summary ? `, ${summary}` : ""}`,
              "tone",
            ];
          }}
        />
        <Line
          type="monotone" dataKey="sentiment" stroke="var(--accent)" strokeWidth={1.5}
          dot={{ r: 2.5, fill: "var(--accent)" }} activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
