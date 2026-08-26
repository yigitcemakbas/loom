interface Props {
  points: number[];
  width?: number;
  height?: number;
}

/** A real, tiny chart of a company's own sentiment history, not decoration.
 * A single trend arrow can't show whether a move was a steady slide or one
 * sharp drop after months of stability; this can, at table-row density. */
export function Sparkline({ points, width = 64, height = 20 }: Props) {
  if (points.length < 2) {
    return <span className="mono value-neutral" style={{ fontSize: 11 }}>-</span>;
  }

  const min = Math.min(...points, -0.05);
  const max = Math.max(...points, 0.05);
  const range = max - min || 1;
  const stepX = width / (points.length - 1);
  const coords = points.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / range) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const first = points[0];
  const last = points[points.length - 1];
  const color = last > first + 0.03 ? "var(--positive)" : last < first - 0.03 ? "var(--negative)" : "var(--text-faint)";
  const zeroY = height - ((0 - min) / range) * height;

  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <line x1={0} y1={zeroY} x2={width} y2={zeroY} stroke="var(--border)" strokeWidth={1} strokeDasharray="2 2" />
      <polyline points={coords.join(" ")} fill="none" stroke={color} strokeWidth={1.25} />
    </svg>
  );
}
