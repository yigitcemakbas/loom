interface Props {
  value: number; // 0..1
}

/** The old UI just said "74% confidence" with no referent, confidence in
 * what? This is explicit: it's the model's estimate that this specific
 * finding is an accurate read of the source filing, not a market
 * prediction or a probability of anything happening. The bar makes the
 * number scannable at a glance across a dense table; the title attribute
 * carries the full explanation for anyone who checks. */
export function Confidence({ value }: Props) {
  const pct = Math.round(value * 100);
  return (
    <span
      className="confidence"
      title="Estimated likelihood that this finding accurately reflects the source filing, not a market prediction."
    >
      <span className="confidence-bar">
        <span className="confidence-bar-fill" style={{ width: `${pct}%` }} />
      </span>
      {pct}%
    </span>
  );
}
