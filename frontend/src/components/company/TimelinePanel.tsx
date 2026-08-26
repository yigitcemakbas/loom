import type { RawDocument } from "../../types/models";

interface Props {
  documents: RawDocument[];
}

const SOURCE_LABELS: Record<RawDocument["source_type"], string> = {
  sec_edgar_filing: "sec",
  news_api: "news",
  scraped_transcript: "transcript",
  scraped_earnings_report: "earnings pr",
};

// Colour carries evidence quality, not decoration. A filing is a legally
// binding disclosure, a transcript is management answering questions it did
// not choose, and a news item is a third party's account of one of those. The
// timeline now mixes all three, so the reader needs to see which kind of
// source a row is without reading the label.
const SOURCE_TAG_CLASS: Record<RawDocument["source_type"], string> = {
  sec_edgar_filing: "tag tag-accent",
  scraped_transcript: "tag tag-positive",
  news_api: "tag",
  scraped_earnings_report: "tag tag-accent",
};

const TYPE_LABELS: Record<string, string> = {
  earnings_call: "earnings call",
  news: "news",
};

/** Chronological view of everything ingested for one company: filings,
 * transcripts, and news interleaved, most recent first. */
export function TimelinePanel({ documents }: Props) {
  if (documents.length === 0) {
    return (
      <div className="empty-state">
        Gathering data for this ticker. Filings usually appear within a minute or two.
      </div>
    );
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th className="num">Date</th>
          <th>Source</th>
          <th>Type</th>
          <th>Filing</th>
        </tr>
      </thead>
      <tbody>
        {documents.map((doc) => (
          <tr key={doc.id} onClick={() => doc.source_url && window.open(doc.source_url, "_blank")}>
            <td className="num mono">
              {doc.published_at ? new Date(doc.published_at).toLocaleDateString() : "-"}
            </td>
            <td>
              <span className={SOURCE_TAG_CLASS[doc.source_type]}>
                {SOURCE_LABELS[doc.source_type]}
              </span>
            </td>
            <td>
              {doc.doc_subtype && (
                <span className="tag">{TYPE_LABELS[doc.doc_subtype] ?? doc.doc_subtype}</span>
              )}
            </td>
            <td>
              {doc.source_url ? (
                <a href={doc.source_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                  {doc.title ?? doc.source_url}
                </a>
              ) : (
                doc.title ?? "untitled document"
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
