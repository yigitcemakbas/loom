import type { RawDocument } from "../../types/models";

interface Props {
  documents: RawDocument[];
}

const SOURCE_LABELS: Record<RawDocument["source_type"], string> = {
  sec_edgar_filing: "SEC",
  news_api: "News",
  scraped_transcript: "Transcript",
  scraped_earnings_report: "Earnings PR",
};

/** Phase 1: raw documents only, chronological. Phase 2 merges in `signals`
 * without changing this component's shape — it already renders anything
 * with a date/title/source/link. */
export function TimelinePanel({ documents }: Props) {
  if (documents.length === 0) {
    // A newly added ticker starts ingesting in the background the moment
    // it's added (see the watchlist "add ticker" flow) — this page polls
    // every 60s, so filings appear here on their own once that finishes.
    return (
      <div className="empty-state">
        Gathering data for this ticker. Filings usually appear within a minute or two.
      </div>
    );
  }

  return (
    <div className="timeline">
      {documents.map((doc) => (
        <div className="timeline-item" key={doc.id}>
          <div className="timeline-date mono">
            {doc.published_at ? new Date(doc.published_at).toLocaleDateString() : "—"}
          </div>
          <div style={{ flex: 1 }}>
            <span className="badge badge-source">{SOURCE_LABELS[doc.source_type]}</span>
            {doc.doc_subtype && <span className="badge badge-source">{doc.doc_subtype}</span>}
            <div>
              {doc.source_url ? (
                <a href={doc.source_url} target="_blank" rel="noreferrer">
                  {doc.title ?? doc.source_url}
                </a>
              ) : (
                <span>{doc.title ?? "Untitled document"}</span>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
