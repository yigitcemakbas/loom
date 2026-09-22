import { useCompanyFactors } from "../../hooks/useFactors";
import type { CompanyFactor } from "../../types/models";

/** What the numbers say, for one company.
 *
 *  This is the half of Loom that does not depend on anything being read. The
 *  filings themselves are analysed for a handful of companies at a time,
 *  because that costs model calls; the figures inside them are arithmetic and
 *  cover the whole universe. So a company with no verdict at all still has a
 *  defensible quantitative read, and this panel is where it lives.
 *
 *  Worst readings first, deliberately. A reader scanning a list stops near the
 *  top, and what a decision most needs is the thing arguing against it. */
export function FactorPanel({ ticker }: { ticker: string }) {
  const { data, isLoading, isError } = useCompanyFactors(ticker);

  if (isLoading) return <div className="panel"><div className="panel-body">Reading the filings…</div></div>;
  if (isError || !data) {
    return (
      <div className="panel">
        <div className="panel-head"><span className="panel-title">The numbers</span></div>
        <p className="empty-state" style={{ padding: "14px 12px", margin: 0 }}>
          {ticker} has not filed enough for Loom to score it. This needs several
          years of statements, not an opinion, so it will fill in on its own.
        </p>
      </div>
    );
  }

  const extremes = data.factors.filter((f) => f.is_extreme);

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">The numbers</span>
        <span className="faint" style={{ fontSize: 9 }}>as at {data.as_of}</span>
      </div>

      <div className="factor-summary">
        <p className="factor-verdict">{data.composite_phrase}</p>
        {data.health && (
          <p className="factor-health">
            Passes <strong>{data.health.passed} of {data.health.available}</strong> standard
            health tests.{" "}
            {data.health.failed.length > 0 && (
              <span className="faint">
                Fails on {data.health.failed.map(readable).join(", ")}.
              </span>
            )}
          </p>
        )}
        {/* The denominator is a property of Loom's data rather than of the
            company, so it is stated rather than left for a reader to assume. */}
        <p className="faint factor-note">
          Measured from figures the company is required to file, not from anything
          Loom read or interpreted. Ranked against comparable companies.
        </p>
      </div>

      {extremes.length > 0 && (
        <>
          <div className="panel-head" style={{ borderTop: "1px solid var(--border)" }}>
            <span className="panel-title">Stands out</span>
          </div>
          <div className="factor-list">
            {extremes.map((f) => <FactorRow key={f.key} factor={f} />)}
          </div>
        </>
      )}

      <div className="panel-head" style={{ borderTop: "1px solid var(--border)" }}>
        <span className="panel-title">Everything measured</span>
        <span className="faint" style={{ fontSize: 9 }}>{data.factors.length}</span>
      </div>
      <div className="factor-list">
        {data.factors.map((f) => <FactorRow key={f.key} factor={f} compact />)}
      </div>
    </div>
  );
}

function FactorRow({ factor, compact }: { factor: CompanyFactor; compact?: boolean }) {
  const pct = factor.percentile;
  const tone = pct === null ? "" : pct >= 0.7 ? "good" : pct <= 0.3 ? "bad" : "mid";

  return (
    <div className={`factor-row ${tone}`}>
      <div className="factor-row-head">
        <span className="factor-label">{factor.label}</span>
        <span className="factor-value">{formatValue(factor)}</span>
      </div>
      {/* The bar is the percentile, never the raw value. Raw values across
          these measures span several orders of magnitude and share no unit,
          so a bar drawn from them would compare nothing to nothing. */}
      {pct !== null && (
        <div className="factor-bar">
          <div className="factor-bar-fill" style={{ width: `${Math.max(pct * 100, 2)}%` }} />
        </div>
      )}
      <div className="factor-row-foot">
        <span className={pct === null ? "faint" : ""}>
          {factor.percentile_phrase ?? "no comparable peer group"}
        </span>
        {factor.universe_size && (
          <span className="faint"> · of {factor.universe_size}</span>
        )}
      </div>
      {!compact && <p className="factor-meaning">{factor.meaning}</p>}
      {!compact && <p className="factor-source">{factor.source}</p>}
    </div>
  );
}

/** Percentages where the number is a rate, multiples where it is a multiple.
 *  Printing "0.8555" for cash conversion and "0.0599" for accruals side by
 *  side invites reading them on the same scale, and they are not.
 *
 *  The valuation yields also get their reciprocal in brackets, because a
 *  reader who knows any single number in finance knows the price-to-earnings
 *  ratio. Loom ranks on the yield (it is continuous through zero, where a P/E
 *  explodes) and shows the multiple, so the ranking stays sound and the
 *  display stays familiar. */
const AS_MULTIPLE = new Set(["cash_conversion"]);
const SHOW_RECIPROCAL: Record<string, string> = {
  earnings_yield: "P/E",
  cash_flow_yield: "P/CF",
  sales_yield: "P/S",
  book_to_price: "P/B",
};

function formatValue(factor: CompanyFactor): string {
  const v = factor.value;
  if (AS_MULTIPLE.has(factor.key)) return `${v.toFixed(2)}x`;

  const percent = `${(v * 100).toFixed(1)}%`;
  const label = SHOW_RECIPROCAL[factor.key];
  // Only where the reciprocal is meaningful. Near zero it runs to hundreds,
  // which is exactly the instability that made the yield the ranked form.
  if (label && v > 0.005) return `${percent}  ${label} ${(1 / v).toFixed(0)}`;
  return percent;
}

function readable(key: string): string {
  return key.replace(/_/g, " ");
}
