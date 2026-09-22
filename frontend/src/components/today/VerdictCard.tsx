import { useState } from "react";
import { Link } from "react-router-dom";
import { useBriefHorizon } from "../../hooks/useBriefs";
import { TrackButton } from "../tracking/TrackButton";
import type { Brief, EarningsOutlook } from "../../types/models";
import {
  confidencePhrase,
  daysUntilPhrase,
  evidenceBreadth,
  rarityPhrase,
  toneClass,
  verdictOf,
} from "../../lib/plain";

interface Props {
  brief: Brief;
  ticker: string;
  name: string;
  earnings?: EarningsOutlook;
}

// The periods an investor actually thinks in. Loom's default view is the one
// year read, which is what the stored brief has always been.
const HORIZONS = [
  { key: "1w", label: "1W" },
  { key: "1m", label: "1M" },
  { key: "1y", label: "1Y" },
  { key: "5y", label: "5Y" },
] as const;

const DEFAULT_HORIZON = "1y";

/** One company's read, written for someone who does not work in finance.
 *
 *  The dense grid this replaces is the right tool for a professional scanning
 *  fifty rows and the wrong one for a person deciding about a single company.
 *  Same underlying verdict, laid out as an argument rather than a row: what
 *  Loom thinks, why, what argues against it, and how much to trust it.
 *
 *  The counterpoint is given the same visual weight as the drivers on purpose.
 *  A view that shows only the reasons for a conclusion is a sales pitch, and a
 *  beginner is exactly the reader least equipped to notice the omission. */
export function VerdictCard({ brief, ticker, name, earnings }: Props) {
  const [horizon, setHorizon] = useState<string>(DEFAULT_HORIZON);
  // The stored brief is the one year read, so the default costs no request.
  const scoped = useBriefHorizon(ticker, horizon === DEFAULT_HORIZON ? null : horizon);
  const shown = (horizon === DEFAULT_HORIZON ? brief : scoped.data) ?? brief;
  const loading = scoped.isFetching && horizon !== DEFAULT_HORIZON;

  const verdict = verdictOf(shown.stance);
  const reporting = earnings ? daysUntilPhrase(earnings.days_until) : null;
  const imminent = earnings?.is_imminent ?? false;

  return (
    <article className="verdict-card">
      <header className="verdict-head">
        <div>
          <Link className="verdict-ticker" to={`/companies/${ticker}`}>{ticker}</Link>
          <span className="verdict-name">{name}</span>
          <TrackButton ticker={ticker} compact />
        </div>
        {reporting && (
          <span className={imminent ? "tag tag-solid" : "tag tag-accent"}>{reporting}</span>
        )}
      </header>

      {/* The period the verdict is about. Without it "leaning negative" is
          unanswerable: the same evidence points opposite ways depending on how
          long a reader intends to hold, and a card with no stated horizon
          invites them to supply their own. */}
      <div className="horizon-row">
        <span className="horizon-hint">over</span>
        {HORIZONS.map((h) => (
          <button
            key={h.key}
            className={`horizon-btn ${horizon === h.key ? "active" : ""}`}
            onClick={() => setHorizon(h.key)}
            aria-pressed={horizon === h.key}
          >
            {h.label}
          </button>
        ))}
        {loading && <span className="horizon-hint">reading…</span>}
      </div>

      <div className={`verdict-label ${toneClass(verdict.tone)}`} style={loading ? { opacity: 0.4 } : undefined}>
        {verdict.label}
      </div>
      <p className="verdict-meaning">{verdict.meaning}</p>

      <p className="verdict-headline">{shown.headline}</p>

      {shown.drivers.length > 0 && (
        <section className="verdict-section">
          <h4>Why</h4>
          <ul>
            {shown.drivers.map((driver) => {
              // Why this finding deserves more weight than its label implies.
              // Shown only when Loom has enough of the company's own history to
              // say so; silence here means no baseline, not "typical".
              const rarity = rarityPhrase(driver.evidence_rate, driver.evidence_sample_size);
              return (
                <li key={driver.title}>
                  <span className={toneClass(
                    driver.direction === "positive" ? "positive"
                    : driver.direction === "negative" ? "negative" : "mixed",
                  )}>
                    {driver.direction === "positive" ? "▲" : driver.direction === "negative" ? "▼" : "■"}
                  </span>{" "}
                  <strong>{driver.title}.</strong> {driver.detail}
                  {rarity && <span className="driver-rarity">{rarity}</span>}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {/* Deliberately as prominent as the reasons above it. */}
      {shown.counterpoint && (
        <section className="verdict-section verdict-against">
          <h4>The other side</h4>
          <p>
            <strong>{shown.counterpoint.title}.</strong> {shown.counterpoint.detail}
          </p>
        </section>
      )}

      {shown.what_changed && (
        <section className="verdict-section">
          <h4>What changed</h4>
          <p>{shown.what_changed}</p>
        </section>
      )}

      <footer className="verdict-foot">
        <span>{confidencePhrase(shown.confidence)}</span>
        <span className="faint">·</span>
        <span>{evidenceBreadth(shown)}</span>
        <span className="faint">·</span>
        <span>
          from {shown.signal_count} finding{shown.signal_count === 1 ? "" : "s"}
        </span>
        <Link className="verdict-more" to={`/companies/${ticker}`}>see the evidence →</Link>
      </footer>
    </article>
  );
}
