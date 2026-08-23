// Mirrors backend/app/schemas/*.py. Phase 1 scope only — no Signal type yet
// (that lands in Phase 2 alongside the engine).

export interface Company {
  id: string;
  ticker: string;
  name: string;
  cik: string | null;
  sector: string | null;
  exchange: string | null;
  created_at: string;
}

export type SourceType =
  | "sec_edgar_filing"
  | "news_api"
  | "scraped_transcript"
  | "scraped_earnings_report";

export interface RawDocument {
  id: string;
  company_id: string;
  source_type: SourceType;
  source_name: string;
  source_url: string | null;
  doc_subtype: string | null;
  title: string | null;
  published_at: string | null;
  fetched_at: string;
}

export interface RawDocumentDetail extends RawDocument {
  content: string;
}

export interface Watchlist {
  id: string;
  name: string;
  owner_email: string | null;
  created_at: string;
}
