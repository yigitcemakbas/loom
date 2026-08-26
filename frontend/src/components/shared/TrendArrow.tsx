interface Props {
  value: number | null;
}

/** A small, real computed delta, not decoration. Absent when there isn't
 * enough history yet to compute one, rather than faking a flat line. */
export function TrendArrow({ value }: Props) {
  if (value === null) return <span className="mono trend-arrow-flat">-</span>;
  if (Math.abs(value) < 0.03) return <span className="mono trend-arrow-flat">flat</span>;
  const up = value > 0;
  return (
    <span className={`mono ${up ? "trend-arrow-up" : "trend-arrow-down"}`}>
      {up ? "▲" : "▼"} {Math.abs(value).toFixed(2)}
    </span>
  );
}
