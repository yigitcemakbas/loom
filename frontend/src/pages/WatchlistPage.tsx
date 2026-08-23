import { useEffect } from "react";
import { AddTickerForm } from "../components/watchlist/AddTickerForm";
import { TickerTable } from "../components/watchlist/TickerTable";
import { useAddTicker, useRemoveTicker, useWatchlistItems, useWatchlists } from "../hooks/useWatchlist";

export function WatchlistPage() {
  const { data: watchlists } = useWatchlists();
  const watchlistId = watchlists?.[0]?.id;

  const { data: companies, isLoading } = useWatchlistItems(watchlistId);
  const addTicker = useAddTicker(watchlistId);
  const removeTicker = useRemoveTicker(watchlistId);

  // Clear the "unknown ticker" error a few seconds after it's shown.
  useEffect(() => {
    if (!addTicker.isError) return;
    const t = setTimeout(() => addTicker.reset(), 5000);
    return () => clearTimeout(t);
  }, [addTicker.isError]);

  return (
    <div>
      <h2>Watchlist</h2>
      <AddTickerForm onAdd={(ticker) => addTicker.mutate(ticker)} isPending={addTicker.isPending} />
      {addTicker.isError && (
        <p style={{ color: "var(--sentiment-negative)" }}>
          Couldn't add that ticker — it needs to be seeded (known to Loom) first.
        </p>
      )}
      <div className="card">
        {isLoading ? (
          <p className="empty-state">Loading…</p>
        ) : (
          <TickerTable
            companies={companies ?? []}
            onRemove={(companyId) => removeTicker.mutate(companyId)}
          />
        )}
      </div>
    </div>
  );
}
