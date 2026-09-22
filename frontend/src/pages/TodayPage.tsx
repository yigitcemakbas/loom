import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useBriefs } from "../hooks/useBriefs";
import { useDashboard } from "../hooks/useDashboard";
import { useUpcomingEarnings } from "../hooks/useEarnings";
import { VerdictCard } from "../components/today/VerdictCard";
import { TickerTape } from "../components/tape/TickerTape";
import { TrendingCharts } from "../components/price/TrendingCharts";
import type { Brief } from "../types/models";

// Loom reaches a real verdict only where it has read enough. Everything else
// is honestly "insufficient", and showing a hundred such cards would bury the
// handful that say something.
const HAS_VIEW = (b: Brief) => b.stance !== "insufficient" && b.stance !== "quiet";

// Strongest convictions first. A beginner reads the top of a page and stops,
// so the ordering is doing most of the editorial work.
const CONVICTION: Record<string, number> = {
  strong_negative: 3, strong_positive: 3,
  negative: 2, positive: 2,
  mixed: 1, quiet: 0, insufficient: 0,
};

/** The simple view: what Loom thinks today, in plain language.
 *
 *  Built around a constraint rather than around a layout. Loom currently holds
 *  a real opinion about a handful of companies and none at all about most, and
 *  a design that hides that ratio would be lying by omission. So the page
 *  leads with what it can defend, says plainly how much is uncovered, and
 *  never pads the gap with cards that mean nothing. */
export function TodayPage() {
  const briefs = useBriefs(200);
  const dashboard = useDashboard();
  const earnings = useUpcomingEarnings();

  const nameById = useMemo(() => {
    const map = new Map<string, { ticker: string; name: string }>();
    for (const row of dashboard.data?.companies ?? []) {
      map.set(row.company_id, { ticker: row.ticker, name: row.name });
    }
    return map;
  }, [dashboard.data]);

  const earningsByTicker = useMemo(() => {
    const map = new Map((earnings.data ?? []).map((e) => [e.ticker, e]));
    return map;
  }, [earnings.data]);

  const all = briefs.data ?? [];
  const withView = useMemo(
    () =>
      all
        .filter(HAS_VIEW)
        .sort(
          (a, b) =>
            (CONVICTION[b.stance] ?? 0) - (CONVICTION[a.stance] ?? 0) ||
            b.confidence - a.confidence,
        ),
    [all],
  );

  // The companies the page is actually about, so the rotating chart follows
  // the argument rather than cycling through names nobody just read about.
  const chartTickers = useMemo(() => {
    const fromVerdicts = withView
      .map((b) => nameById.get(b.company_id)?.ticker)
      .filter((t): t is string => Boolean(t));
    if (fromVerdicts.length > 0) return fromVerdicts;
    return (dashboard.data?.companies ?? []).slice(0, 8).map((c) => c.ticker);
  }, [withView, nameById, dashboard.data]);

  const uncovered = all.length - withView.length;
  const reportingSoon = (earnings.data ?? []).filter(
    (e) => e.days_until !== null && e.days_until <= 14,
  );

  if (briefs.isLoading) {
    return <p className="empty-state">Loading…</p>;
  }

  return (
    <div>
      <TickerTape />

      <header className="today-head">
        <h1>
          {withView.length === 0
            ? "Nothing to decide today"
            : `${withView.length} ${withView.length === 1 ? "company" : "companies"} worth a look`}
        </h1>
        <p className="today-sub">
          {withView.length === 0
            ? "Loom has read the filings and found nothing that points clearly either way. That is a real answer, not a gap."
            : "Loom read the filings, transcripts, insider trades and news, and these are the companies where the evidence actually points somewhere."}
        </p>
      </header>

      {reportingSoon.length > 0 && (
        <div className="today-banner">
          <strong>{reportingSoon.length}</strong>{" "}
          {reportingSoon.length === 1 ? "company reports" : "companies report"} in the next two weeks:{" "}
          {reportingSoon.slice(0, 6).map((e, i) => (
            <span key={e.ticker}>
              {i > 0 && ", "}
              <Link to={`/companies/${e.ticker}`}>{e.ticker}</Link>
            </span>
          ))}
          . Results move prices more than anything else Loom watches.
        </div>
      )}

      <div className="verdict-grid">
        {withView.map((brief) => {
          const company = nameById.get(brief.company_id);
          if (!company) return null;
          return (
            <VerdictCard
              key={brief.id}
              brief={brief}
              ticker={company.ticker}
              name={company.name}
              earnings={earningsByTicker.get(company.ticker)}
            />
          );
        })}
      </div>

      {/* Prices sit below the verdicts rather than above them. What a stock
          did is context for a judgement, not a substitute for one, and leading
          with a moving chart invites reading the squiggle instead of the
          argument. */}
      {chartTickers.length > 0 && (
        <div className="today-prices">
          <TrendingCharts tickers={chartTickers} />
        </div>
      )}

      {/* The ratio is the honest part. Most of the universe is tracked for
          numbers but has not been read, and a reader deserves to know the
          difference between "nothing wrong" and "not looked at". */}
      {uncovered > 0 && (
        <p className="today-uncovered">
          Loom tracks {all.length} companies and has read enough to form a view on{" "}
          {withView.length}. The other {uncovered} are being watched for filings, insider
          trades and short interest, but have not been analysed deeply enough to say
          anything yet. <Link to="/terminal">See everything in the terminal →</Link>
        </p>
      )}
    </div>
  );
}
