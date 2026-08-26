// Mirrors backend/app/schemas/*.py. Phase 1 scope only, no Signal type yet
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

export interface RawDocumentWithContext extends RawDocument {
  ticker: string;
}

export interface SearchHit extends RawDocumentWithContext {
  rank: number;
  /** The passage that matched, so a hit can be judged without opening it.
   *  Null when the match came from a stemmed form the snippet scan missed. */
  snippet: string | null;
}

export interface Watchlist {
  id: string;
  name: string;
  owner_email: string | null;
  created_at: string;
}

export type SignalType =
  | "sentiment_shift"
  | "new_risk_factor"
  | "notable_quote"
  | "qoq_anomaly"
  | "guidance_change"
  | "emerging_pattern"
  | "insider_activity"
  | "short_interest_spike";

export type MarketDirection = "positive" | "negative" | "neutral";
export type MarketMagnitude = "minor" | "moderate" | "major";
export type MarketHorizon = "near_term" | "multi_quarter" | "structural";

export interface Signal {
  id: string;
  company_id: string;
  signal_type: SignalType;
  summary: string;
  detail: string | null;
  market_direction: MarketDirection | null;
  market_magnitude: MarketMagnitude | null;
  market_horizon: MarketHorizon | null;
  sentiment_score: number | null;
  confidence: number;
  priority: number;
  evidence_quote: string | null;
  source_document_id: string | null;
  compared_document_id: string | null;
  occurred_at: string;
  created_at: string;
  reviewed_at: string | null;
  dismissed_at: string | null;
  note: string | null;
  ticker: string;
  source_url: string | null;
  doc_subtype: string | null;
  compared_source_url: string | null;
  pattern_document_count: number | null;
  pattern_window_days: number | null;
}

export interface SentimentPoint {
  occurred_at: string;
  sentiment_score: number;
  summary: string;
}

export interface AnalysisTriggerResponse {
  ticker: string;
  documents_queued: number;
  detail: string;
}

export interface CompanyDashboardRow {
  company_id: string;
  ticker: string;
  name: string;
  sector: string | null;
  sentiment_score: number | null;
  sentiment_trend: number | null;
  sentiment_history: number[];
  signal_count: number;
  risk_count: number;
  top_signal: Signal | null;
  last_signal_at: string | null;
  bearish_count: number;
  bullish_count: number;
  major_count: number;
  pattern_count: number;
  insider_net_usd: number;
  top_priority: number;
  avg_confidence: number | null;
}

export interface PortfolioSummary {
  companies_total: number;
  companies_covered: number;
  total_risk_count: number;
  avg_sentiment: number | null;
  trend_up: number;
  trend_down: number;
  most_active_ticker: string | null;
  most_active_signal_count: number;
}

export interface DashboardResponse {
  portfolio: PortfolioSummary;
  companies: CompanyDashboardRow[];
}

export type AnalysisStatus = "completed" | "failed";

export interface AnalysisRun {
  id: string;
  ticker: string;
  doc_subtype: string | null;
  prompt_version: string;
  status: AnalysisStatus;
  error: string | null;
  signal_count: number;
  created_at: string;
}

export interface UsageRun {
  id: string;
  ticker: string;
  provider: string;
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  documents_analyzed: number;
  created_at: string;
}

export interface SystemStatus {
  analysis_runs: AnalysisRun[];
  total_runs: number;
  failed_runs: number;
  usage_runs: UsageRun[];
  total_cost_usd: number;
  total_calls: number;
}

export type FactType =
  | "insider_transaction"
  | "institutional_holding"
  | "short_interest"
  | "patent_filing"
  | "search_trend_index"
  | "job_posting_count"
  | "app_store_ranking";

export interface StructuredFact {
  id: string;
  company_id: string;
  fact_type: FactType;
  source_name: string;
  source_url: string | null;
  as_of_date: string;
  value: number | null;
  unit: string | null;
  attributes: Record<string, unknown>;
  fetched_at: string;
}

/** Open-market figures are separate from the raw count on purpose: most Form 4
 *  activity is tax withholding on vesting shares and option exercises, so a
 *  single headline number would overstate what insiders actually decided. */
export interface InsiderActivitySummary {
  transactions: number;
  open_market_transactions: number;
  open_market_sold_usd: number;
  open_market_bought_usd: number;
  distinct_insiders: number;
  latest_transaction_date: string | null;
}

export type Stance =
  | "strong_negative" | "negative" | "mixed"
  | "positive" | "strong_positive" | "quiet" | "insufficient";

export interface BriefDriver {
  title: string;
  detail: string;
  direction: string;
  magnitude: string;
  sources: string[];
  signal_ids: string[];
}

/** The product's actual deliverable: one company's current read, folded from
 *  every filing, transcript, news item, and insider trade about it. */
export interface Brief {
  id: string;
  company_id: string;
  stance: Stance;
  stance_label: string;
  headline: string;
  confidence: number;
  drivers: BriefDriver[];
  what_changed: string | null;
  source_types: string[];
  source_labels: string[];
  signal_count: number;
  evidence: Record<string, unknown>;
  generated_at: string;
}

/** The read for a company's next earnings event. `reports_seen` is 0 on a free
 *  Finnhub key, which supplies scheduled dates and consensus but no historical
 *  actuals, so the track record is hidden rather than rendered empty. */
export interface EarningsOutlook {
  ticker: string;
  next_date: string | null;
  days_until: number | null;
  when_label: string | null;
  eps_estimate: number | null;
  revenue_estimate: number | null;
  quarter_label: string | null;
  is_imminent: boolean;
  reports_seen: number;
  beats: number;
  misses: number;
  average_surprise_percent: number | null;
  last_surprise_percent: number | null;
  headline: string;
}

export interface PricePoint {
  t: number;
  c: number;
}

export interface PriceSeries {
  ticker: string;
  range: string;
  currency: string | null;
  points: PricePoint[];
  previous_close: number | null;
  last: number | null;
  change: number | null;
  change_percent: number | null;
}
