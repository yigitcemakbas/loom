interface Props {
  value: number | null;
}

function colorClass(v: number): string {
  if (v > 0.15) return "value-positive";
  if (v < -0.15) return "value-negative";
  return "value-neutral";
}

export function SentimentValue({ value }: Props) {
  if (value === null) return <span className="mono value-neutral">-</span>;
  const sign = value > 0 ? "+" : "";
  return <span className={`mono ${colorClass(value)}`}>{sign}{value.toFixed(2)}</span>;
}
