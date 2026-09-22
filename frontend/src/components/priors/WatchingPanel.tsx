import { useCompanyPrior } from "../../hooks/usePriors";

/** What Loom decided, in advance, would be significant for this company.
 *
 *  The fast path's whole claim is that the expensive thinking happens before
 *  an event arrives, so that when a filing lands the judgement is arithmetic
 *  and takes milliseconds. Until now the interface showed only the second
 *  half: a score appeared and a reader had no way to see what standing
 *  expectation produced it, which makes the score an assertion rather than a
 *  conclusion.
 *
 *  The literal keywords are shown on purpose. They are unglamorous and they
 *  are the thing that makes the claim checkable. */
export function WatchingPanel({ ticker }: { ticker: string }) {
  const { data, isLoading, isError } = useCompanyPrior(ticker);

  if (isLoading) return null;

  if (isError || !data) {
    return (
      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">What Loom is watching for</span>
        </div>
        {/* Said plainly rather than hidden. A company with no standing view
            scores every filing zero, which looks exactly like a quiet week,
            and a reader who cannot tell those apart is being misled. */}
        <p className="empty-state" style={{ padding: "12px", margin: 0 }}>
          Loom has not built a standing view for {ticker} yet. Until it does,
          filings from this company are stored and read but not scored against
          anything, so a quiet week and an unwatched week look the same here.
        </p>
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">What Loom is watching for</span>
        <span className="faint" style={{ fontSize: 9 }}>
          {data.matched} filings scored · {data.notable} notable
        </span>
      </div>

      <p className="prior-summary">{data.summary}</p>

      <div className="watch-list">
        {data.watch_items.map((item) => (
          <article key={item.topic} className={`watch-item ${item.direction ?? ""}`}>
            <div className="watch-head">
              <span className="watch-topic">{item.topic}</span>
              {item.severity && <span className="watch-severity">{item.severity}</span>}
            </div>
            {item.why_it_matters && <p className="watch-why">{item.why_it_matters}</p>}
            {item.keywords.length > 0 && (
              <p className="watch-keywords">
                {item.keywords.map((k) => (
                  <span key={k} className="keyword">{k}</span>
                ))}
              </p>
            )}
          </article>
        ))}
      </div>

      <p className="faint watch-note">
        Decided before these events arrived, from {data.source_signal_count} findings,
        and not changed since. That is what lets Loom score an arriving filing in
        milliseconds instead of minutes, and it is also the honest limit: Loom can
        only recognise what it thought to watch for.
      </p>
    </div>
  );
}
