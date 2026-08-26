import { useEffect, useState } from "react";
import { useDocumentSearch, useFilings } from "../hooks/useFilings";
import type { RawDocumentWithContext, SearchHit } from "../types/models";

// Values are the stored doc_subtype; labels exist because "earnings_call"
// renders as an underscored shout next to "10-K" otherwise.
const TYPES: { value: string; label: string }[] = [
  { value: "", label: "all types" },
  { value: "10-K", label: "10-K" },
  { value: "10-Q", label: "10-Q" },
  { value: "8-K", label: "8-K" },
  { value: "earnings_call", label: "earnings call" },
  { value: "news", label: "news" },
];

const TYPE_LABELS: Record<string, string> = {
  earnings_call: "earnings call",
  news: "news",
};

function isSearchHit(doc: RawDocumentWithContext | SearchHit): doc is SearchHit {
  return "snippet" in doc;
}

/** Cross-portfolio filing browser and full-text search.
 *
 *  Search runs over document *content*, not just metadata: the index is a
 *  Postgres tsvector built at ingestion (see backend SearchRepository), so a
 *  query for a phrase buried in a 10-K finds it. Typing a query switches this
 *  page from browse mode to results mode; clearing it switches back, which
 *  keeps one page rather than splitting a browser and a search page that show
 *  the same rows. */
export function FilingsPage() {
  const [ticker, setTicker] = useState("");
  const [docSubtype, setDocSubtype] = useState("");
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");

  // Debounced: full-text search is a real query, not worth running on every
  // keystroke of "gross margin".
  useEffect(() => {
    const timer = setTimeout(() => setQuery(queryInput), 250);
    return () => clearTimeout(timer);
  }, [queryInput]);

  const searching = query.trim().length >= 2;

  const browse = useFilings({
    ticker: ticker || undefined,
    doc_subtype: docSubtype || undefined,
  });
  const search = useDocumentSearch({
    q: query,
    ticker: ticker || undefined,
    doc_subtype: docSubtype || undefined,
  });

  const active = searching ? search : browse;
  const rows: (RawDocumentWithContext | SearchHit)[] = active.data ?? [];

  return (
    <div>
      <div className="page-title-row">
        <h2>Filings</h2>
      </div>

      <div className="filter-bar">
        <input
          type="text"
          placeholder="search filing text"
          value={queryInput}
          onChange={(e) => setQueryInput(e.target.value)}
          style={{ width: 240 }}
        />
        <input
          type="text"
          placeholder="filter by ticker"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          style={{ width: 110 }}
        />
        <select value={docSubtype} onChange={(e) => setDocSubtype(e.target.value)}>
          {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        {searching && (
          <span className="mono" style={{ color: "var(--text-faint)", fontSize: 11 }}>
            {active.isLoading ? "searching…" : `${rows.length} match${rows.length === 1 ? "" : "es"}`}
          </span>
        )}
      </div>

      {active.isError && (
        <p className="error-text">Can't reach the Loom API. Confirm the backend is running and reload.</p>
      )}

      <div className="panel">
        {active.isLoading ? (
          <p className="empty-state">Loading…</p>
        ) : rows.length > 0 ? (
          <table className="data-table">
            <thead>
              <tr>
                <th className="num">Date</th>
                <th>Ticker</th>
                <th>Type</th>
                <th>{searching ? "Match" : "Filing"}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.id} onClick={() => f.source_url && window.open(f.source_url, "_blank")}>
                  <td className="num mono">
                    {f.published_at ? new Date(f.published_at).toLocaleDateString() : "-"}
                  </td>
                  <td className="ticker-symbol">{f.ticker}</td>
                  <td>
                    {f.doc_subtype && (
                      <span className="tag">{TYPE_LABELS[f.doc_subtype] ?? f.doc_subtype}</span>
                    )}
                  </td>
                  <td>
                    {f.source_url ? (
                      <a href={f.source_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                        {f.title ?? f.source_url}
                      </a>
                    ) : (
                      f.title ?? "untitled document"
                    )}
                    {isSearchHit(f) && f.snippet && (
                      <div
                        style={{
                          marginTop: 4,
                          fontSize: 12,
                          lineHeight: 1.5,
                          color: "var(--text-dim)",
                        }}
                      >
                        {f.snippet}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state">
            {searching ? `Nothing matches "${query}".` : "No filings match this filter."}
          </div>
        )}
      </div>
    </div>
  );
}
