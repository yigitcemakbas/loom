import { isAxiosError } from "axios";
import { useEffect, useMemo, useState } from "react";
import { AddTickerForm } from "../components/watchlist/AddTickerForm";
import { BriefCard } from "../components/brief/BriefCard";
import { SetupBanner } from "../components/layout/SetupBanner";
import { TrendingCharts } from "../components/price/TrendingCharts";
import { ScreenerTable } from "../components/dashboard/ScreenerTable";
import { TickerTape } from "../components/tape/TickerTape";
import { useBriefs } from "../hooks/useBriefs";
import { useDashboard } from "../hooks/useDashboard";
import { useLastUpdated } from "../hooks/useLastUpdated";
import { useAddTicker, useRemoveTicker, useWatchlists } from "../hooks/useWatchlist";

function addTickerErrorMessage(error: unknown): string {
  if (isAxiosError(error) && typeof error.response?.data?.detail === "string") {
    return error.response.data.detail;
  }
  if (error instanceof Error) return error.message;
  return "Couldn't add that ticker.";
}

type View = "briefs" | "data";

/** The home screen answers one question: which of my companies needs me today?
 *
 *  It leads with a verdict per company, worst first, because a list of findings
 *  is not a decision aid: a reader given forty-five sentences about Apple
 *  learns less than one given a single sentence saying which way the evidence
 *  leans. The dense screener is still here, one click away, for the case where
 *  the reader already knows what they are looking for and wants to compare
 *  numbers across the whole watchlist. */
export function DashboardPage() {
  const [view, setView] = useState<View>("briefs");

  const { data: watchlists, isError: watchlistsError } = useWatchlists();
  const watchlistId = watchlists?.[0]?.id;

  const briefs = useBriefs();
  const dashboard = useDashboard();
  const lastUpdated = useLastUpdated(briefs.dataUpdatedAt);

  const addTicker = useAddTicker(watchlistId);
  const removeTicker = useRemoveTicker(watchlistId);

  useEffect(() => {
    if (!addTicker.isError) return;
    const t = setTimeout(() => addTicker.reset(), 5000);
    return () => clearTimeout(t);
  }, [addTicker.isError]);

  // The brief carries no company name, and asking the API for one per card
  // would be a request per row; the dashboard already has them.
  const nameFor = useMemo(() => {
    const map = new Map<string, { ticker: string; name: string }>();
    for (const row of dashboard.data?.companies ?? []) {
      map.set(row.company_id, { ticker: row.ticker, name: row.name });
    }
    return map;
  }, [dashboard.data]);

  const rotationTickers = useMemo(
    () =>
      (briefs.data ?? [])
        .map((b) => nameFor.get(b.company_id)?.ticker)
        .filter((t): t is string => Boolean(t)),
    [briefs.data, nameFor],
  );

  const needsAttention = (briefs.data ?? []).filter(
    (b) => b.stance !== "insufficient" && b.stance !== "quiet",
  );
  const rest = (briefs.data ?? []).filter(
    (b) => b.stance === "insufficient" || b.stance === "quiet",
  );

  return (
    <div>
      <div className="page-title-row">
        <h2>Where things stand</h2>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="faint" style={{ fontSize: 10 }}>UPD {lastUpdated}</span>
          <button
            className="btn"
            style={view === "briefs" ? { background: "var(--accent)", color: "#14100a" } : undefined}
            onClick={() => setView("briefs")}
          >
            briefs
          </button>
          <button
            className="btn"
            style={view === "data" ? { background: "var(--accent)", color: "#14100a" } : undefined}
            onClick={() => setView("data")}
          >
            data
          </button>
          <AddTickerForm
            onAdd={(ticker) => addTicker.mutate(ticker)}
            disabled={addTicker.isPending || !watchlistId}
            label={addTicker.isPending ? "adding…" : !watchlistId ? "…" : "add"}
          />
        </div>
      </div>

      {(watchlistsError || briefs.isError) && (
        <p className="error-text">Can't reach the Loom API. Confirm the backend is running and reload.</p>
      )}
      {addTicker.isError && <p className="error-text">{addTickerErrorMessage(addTicker.error)}</p>}

      <SetupBanner />

      {/* Above the view switch, unlike the strip it replaces: what is about to
          happen and what just did are context for both views, not just one. */}
      <TickerTape />

      {view === "data" ? (
        <ScreenerTable
          rows={dashboard.data?.companies ?? []}
          isLoading={dashboard.isLoading}
          onRemove={(id) => removeTicker.mutate(id)}
        />
      ) : briefs.isLoading ? (
        <p className="empty-state">Reading your companies…</p>
      ) : (
        <div className="grid grid-main-side">
        <div className="stack">
          {needsAttention.length > 0 && (
            <>
              <div className="section-label">
                Needs a look ({needsAttention.length})
              </div>
              {needsAttention.map((b) => {
                const meta = nameFor.get(b.company_id);
                return (
                  <BriefCard
                    key={b.id}
                    brief={b}
                    ticker={meta?.ticker ?? "?"}
                    name={meta?.name ?? ""}
                  />
                );
              })}
            </>
          )}

          {rest.length > 0 && (
            <>
              <div className="section-label" style={{ marginTop: 4 }}>
                Nothing to act on ({rest.length})
              </div>
              {rest.map((b) => {
                const meta = nameFor.get(b.company_id);
                return (
                  <BriefCard
                    key={b.id}
                    brief={b}
                    ticker={meta?.ticker ?? "?"}
                    name={meta?.name ?? ""}
                    compact
                  />
                );
              })}
            </>
          )}

          {needsAttention.length === 0 && rest.length === 0 && (
            <div className="empty-state">
              No companies yet. Add a ticker above, it resolves and starts ingesting automatically.
            </div>
          )}
        </div>

        <div className="stack">
          <TrendingCharts tickers={rotationTickers} />
        </div>
        </div>
      )}
    </div>
  );
}
