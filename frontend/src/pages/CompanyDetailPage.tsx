import { useParams } from "react-router-dom";
import { TimelinePanel } from "../components/company/TimelinePanel";
import { useCompany, useCompanyTimeline } from "../hooks/useCompanyDetail";

/** Phase 1: header + raw timeline only. SentimentTrendChart and
 * RiskFactorDiffCard land in Phase 2 once the engine produces signals —
 * this page is structured so they slot in above/beside TimelinePanel
 * without a rewrite. */
export function CompanyDetailPage() {
  const { ticker } = useParams<{ ticker: string }>();
  const { data: company, isLoading: companyLoading } = useCompany(ticker);
  const { data: documents, isLoading: timelineLoading } = useCompanyTimeline(ticker);

  if (companyLoading) return <p className="empty-state">Loading…</p>;
  if (!company) return <p className="empty-state">Unknown ticker {ticker}</p>;

  return (
    <div>
      <h2>
        {company.name} <span className="mono" style={{ color: "var(--text-secondary)" }}>{company.ticker}</span>
      </h2>
      <p style={{ color: "var(--text-secondary)" }}>
        {company.sector ?? "—"} · {company.exchange ?? "—"}
      </p>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Timeline</h3>
        {timelineLoading ? (
          <p className="empty-state">Loading…</p>
        ) : (
          <TimelinePanel documents={documents ?? []} />
        )}
      </div>
    </div>
  );
}
