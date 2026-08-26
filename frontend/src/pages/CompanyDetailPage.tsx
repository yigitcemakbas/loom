import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { ActivityPanel } from "../components/company/ActivityPanel";
import { BriefCard } from "../components/brief/BriefCard";
import { CompanyPricePanel } from "../components/price/CompanyPricePanel";
import { SentimentTrendChart } from "../components/company/SentimentTrendChart";
import { TimelinePanel } from "../components/company/TimelinePanel";
import { SignalTable } from "../components/signals/SignalTable";
import { useCompany, useCompanyTimeline } from "../hooks/useCompanyDetail";
import { useBrief } from "../hooks/useBriefs";
import { useCompanyEarnings } from "../hooks/useEarnings";
import { useInsiderActivity } from "../hooks/useFacts";
import { useSentimentSeries, useSignals, useTriggerAnalysis } from "../hooks/useSignals";

function compactUsd(v: number): string {
  if (!v) return "-";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(abs / 1e3).toFixed(0)}K`;
  return `$${abs.toFixed(0)}`;
}

/** One company as a terminal screen rather than a document.
 *
 *  The identity line and figure strip sit at the top the way a quote page
 *  does, then the working area is a two-column grid so the findings, the tone
 *  chart, the insider record, and the filing list are all visible at once.
 *  The previous single scrolling column meant answering "what is going on with
 *  this company" required three screens of reading. */
export function CompanyDetailPage() {
  const { ticker } = useParams<{ ticker: string }>();
  const { data: company, isLoading: companyLoading } = useCompany(ticker);
  const { data: documents, isLoading: timelineLoading } = useCompanyTimeline(ticker);
  const { data: signals } = useSignals({ ticker });
  const { data: sentiment } = useSentimentSeries(ticker);
  const { data: insider } = useInsiderActivity(ticker ?? "", 90);
  const { data: brief } = useBrief(ticker);
  const { data: earnings } = useCompanyEarnings(ticker);
  const analyze = useTriggerAnalysis(ticker);

  const stats = useMemo(() => {
    const list = signals ?? [];
    const latest = sentiment && sentiment.length ? sentiment[sentiment.length - 1].sentiment_score : null;
    const prior = sentiment && sentiment.length > 1 ? sentiment[sentiment.length - 2].sentiment_score : null;
    return {
      latest,
      delta: latest !== null && prior !== null ? latest - prior : null,
      signals: list.length,
      risks: list.filter((s) => s.signal_type === "new_risk_factor" || s.signal_type === "qoq_anomaly").length,
      bearish: list.filter((s) => s.market_direction === "negative").length,
      bullish: list.filter((s) => s.market_direction === "positive").length,
      major: list.filter((s) => s.market_magnitude === "major").length,
      patterns: list.filter((s) => s.signal_type === "emerging_pattern").length,
    };
  }, [signals, sentiment]);

  if (companyLoading) return <p className="empty-state">Loading…</p>;
  if (!company) return <p className="empty-state">Unknown ticker {ticker}</p>;

  const insiderNet = insider ? insider.open_market_bought_usd - insider.open_market_sold_usd : 0;

  return (
    <div>
      <div className="quote-header">
        <span className="quote-ticker">{company.ticker}</span>
        <span className="quote-name">{company.name}</span>
        <span className="quote-meta">{company.sector ?? "-"} · {company.exchange ?? "-"}</span>
        <span style={{ marginLeft: "auto" }}>
          <button className="btn" onClick={() => analyze.mutate()} disabled={analyze.isPending}>
            {analyze.isPending ? "starting…" : "analyse"}
          </button>
        </span>
      </div>

      {earnings?.next_date && (
        <div
          className="panel"
          style={{
            margin: "8px 0",
            borderLeft: `3px solid ${earnings.is_imminent ? "var(--accent)" : "var(--border-strong)"}`,
            padding: "6px 10px",
          }}
        >
          <span className={earnings.is_imminent ? "tag tag-solid" : "tag tag-accent"}>
            {earnings.days_until === 0 ? "TODAY" : earnings.days_until === 1 ? "TOMORROW" : `${earnings.days_until}D`}
          </span>
          <span className="sans" style={{ marginLeft: 8, fontSize: 12 }}>{earnings.headline}</span>
        </div>
      )}

      {brief && (
        <div style={{ margin: "8px 0" }}>
          <BriefCard brief={brief} ticker={company.ticker} name={company.name} showIdentity={false} />
        </div>
      )}

      <div className="stat-strip" style={{ marginBottom: 8 }}>
        <Stat
          label="Sentiment"
          value={stats.latest === null ? "-" : stats.latest.toFixed(2)}
          tone={stats.latest === null ? undefined : stats.latest >= 0 ? "pos" : "neg"}
          sub={stats.delta === null ? undefined : `${stats.delta > 0 ? "+" : ""}${stats.delta.toFixed(2)} vs prior`}
        />
        <Stat label="Signals" value={String(stats.signals)} sub="all time" />
        <Stat label="Risk findings" value={String(stats.risks)} tone={stats.risks ? "neg" : undefined} />
        <Stat label="Bearish / bullish" value={`${stats.bearish} / ${stats.bullish}`} />
        <Stat label="Major" value={String(stats.major)} tone={stats.major ? "neg" : undefined} />
        <Stat label="Patterns" value={String(stats.patterns)} tone={stats.patterns ? "accent" : undefined} />
        <Stat
          label="Insider net 90d"
          value={insider ? (insiderNet === 0 ? "-" : `${insiderNet < 0 ? "-" : "+"}${compactUsd(insiderNet)}`) : "-"}
          tone={insiderNet === 0 ? undefined : insiderNet > 0 ? "pos" : "neg"}
          sub={insider ? `${insider.distinct_insiders} insiders` : undefined}
        />
        <Stat label="Documents" value={String(documents?.length ?? 0)} sub="ingested" />
      </div>

      {analyze.isSuccess && (
        <p className="notice" style={{ marginBottom: 8 }}>
          Analysing {analyze.data.documents_queued} recent filings. Signals appear here as each finishes.
        </p>
      )}
      {analyze.isError && (
        <p className="error-text">
          Couldn't start analysis. The usual causes are a missing provider API key or exhausted quota.
        </p>
      )}

      <div className="grid grid-main-side">
        <div className="stack">
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Signals</span>
              <span className="faint" style={{ fontSize: 10 }}>{signals?.length ?? 0}</span>
            </div>
            <SignalTable
              signals={signals ?? []}
              showTicker={false}
              emptyMessage='No signals yet. Choose "analyse" above to generate them.'
            />
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Documents</span>
              <span className="faint" style={{ fontSize: 10 }}>{documents?.length ?? 0}</span>
            </div>
            {timelineLoading ? (
              <p className="empty-state">Loading…</p>
            ) : (
              <TimelinePanel documents={documents ?? []} />
            )}
          </div>
        </div>

        <div className="stack">
          <CompanyPricePanel ticker={company.ticker} />

          <div className="panel">
            <div className="panel-head"><span className="panel-title">Management tone</span></div>
            <div className="panel-body">
              <SentimentTrendChart points={sentiment ?? []} />
            </div>
          </div>

          <ActivityPanel ticker={ticker ?? ""} />
        </div>
      </div>
    </div>
  );
}

function Stat({
  label, value, sub, tone,
}: { label: string; value: string; sub?: string; tone?: "pos" | "neg" | "accent" }) {
  const color =
    tone === "pos" ? "var(--positive)"
    : tone === "neg" ? "var(--negative)"
    : tone === "accent" ? "var(--accent)"
    : "var(--text)";
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color }}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}
