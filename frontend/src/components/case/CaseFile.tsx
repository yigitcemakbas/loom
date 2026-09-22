import { useState } from "react";
import { useCase } from "../../hooks/useCase";
import { verdictOf, confidencePhrase, toneClass } from "../../lib/plain";
import type { CasePoint, Stance } from "../../types/models";

/** The whole argument about one company, in the order it should be read.
 *
 *  This replaces a stack of six panels that each answered a different question
 *  and left the reader to work out which of thirty things should change their
 *  mind. That synthesis is the work the product exists to do, and leaving it
 *  undone made Loom a very good instrument panel with nobody flying the plane.
 *
 *  Ordering is the whole design. A reader scanning a list stops near the top,
 *  so what sits there is doing more for them than any amount of detail
 *  further down. Disagreement between independent sources comes first, then
 *  readings that are unusual for this company, then readings that are merely
 *  large. */
export function CaseFilePanel({ ticker }: { ticker: string }) {
  const { data, isLoading } = useCase(ticker);
  if (isLoading || !data) return null;

  const verdict = data.stance ? verdictOf(data.stance as Stance) : null;

  return (
    <section className="case">
      <header className="case-head">
        <div className="case-verdict-block">
          {verdict && (
            <span className={`case-verdict ${toneClass(verdict.tone)}`}>{verdict.label}</span>
          )}
          {data.held && <span className="case-held">you hold this</span>}
        </div>
        <p className="case-headline">{data.headline}</p>
        {verdict && (
          <p className="case-confidence">
            {confidencePhrase(data.confidence)} · {data.points.length} things worth knowing
          </p>
        )}
      </header>

      {/* Given its own place above the list rather than a slot inside it. A
          page showing only the case for a conclusion is a sales pitch, and
          this is the point a decision most needs. */}
      {data.strongest_against && (
        <div className="case-counter">
          <span className="case-counter-label">The best argument the other way</span>
          <p className="case-counter-headline">{data.strongest_against.headline}</p>
          {data.strongest_against.detail && (
            <p className="case-counter-detail">{data.strongest_against.detail}</p>
          )}
        </div>
      )}

      <ol className="case-points">
        {data.points.map((point, index) => (
          <CaseRow key={point.key} point={point} rank={index + 1} />
        ))}
      </ol>

      {data.valuation.length > 0 && (
        <div className="case-section">
          <h4>What you would be paying</h4>
          {/* Separated because it answers a different question from everything
              above it. A reader who conflates the two buys a good company at
              any price. */}
          <ol className="case-points">
            {data.valuation.map((point) => (
              <CaseRow key={point.key} point={point} />
            ))}
          </ol>
        </div>
      )}

      {data.gaps.length > 0 && (
        <div className="case-gaps">
          <h4>What Loom has not done here</h4>
          {data.gaps.map((gap) => <p key={gap}>{gap}</p>)}
        </div>
      )}
    </section>
  );
}

function CaseRow({ point, rank }: { point: CasePoint; rank?: number }) {
  const [open, setOpen] = useState(false);
  const mark = point.side === "for" ? "▲" : point.side === "against" ? "▼" : "◆";
  const tone = point.side === "for" ? "for" : point.side === "against" ? "against" : "unclear";

  return (
    <li className={`case-point ${tone}`}>
      <span className="case-mark" aria-hidden>{mark}</span>
      <div className="case-body">
        <p className="case-point-headline">
          {rank !== undefined && <span className="case-rank">{rank}</span>}
          {point.headline}
        </p>
        {point.detail && <p className="case-point-detail">{point.detail}</p>}

        <div className="case-meta">
          <span className="case-source">from {point.source}</span>
          {(point.quote || point.settles_it) && (
            <button className="case-toggle" onClick={() => setOpen(!open)}>
              {open ? "less" : "show me"}
            </button>
          )}
        </div>

        {open && (
          <div className="case-evidence">
            {/* The receipt. Nothing reaches this page without one where a
                document produced it. */}
            {point.quote && <blockquote className="case-quote">{point.quote}</blockquote>}
            {point.settles_it && (
              <p className="case-settles">
                <strong>What would settle it:</strong> {point.settles_it}
              </p>
            )}
          </div>
        )}
      </div>
    </li>
  );
}
