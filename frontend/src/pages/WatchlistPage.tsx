import { isAxiosError } from "axios";
import { useEffect } from "react";
import { AddTickerForm } from "../components/watchlist/AddTickerForm";
import { TickerTable } from "../components/watchlist/TickerTable";
import { useAddTicker, useRemoveTicker, useWatchlistItems, useWatchlists } from "../hooks/useWatchlist";

/** Prefer the API's own error detail (e.g. "'ZZZZ' isn't a recognized
 * ticker.") over a generic message, since the backend already resolves
 * unknown tickers against SEC's ticker directory and knows exactly why
 * a given one failed. */
function addTickerErrorMessage(error: unknown): string {
  if (isAxiosError(error) && typeof error.response?.data?.detail === "string") {
    return error.response.data.detail;
  }
  if (error instanceof Error) return error.message;
  return "Couldn't add that ticker.";
}

export function WatchlistPage() {
  const { data: watchlists, isError: watchlistsError } = useWatchlists();
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

  const addButtonLabel = addTicker.isPending
    ? "Adding…"
    : !watchlistId
      ? "Loading…"
      : "Add ticker";

  return (
    <div>
      <h2>Watchlist</h2>

      {watchlistsError && (
        <p style={{ color: "var(--sentiment-negative)" }}>
          Can't reach the Loom API. Confirm the backend is running (uvicorn on port 8000) and
          reload this page.
        </p>
      )}

      <AddTickerForm
        onAdd={(ticker) => addTicker.mutate(ticker)}
        disabled={addTicker.isPending || !watchlistId}
        label={addButtonLabel}
      />
      {addTicker.isError && (
        <p style={{ color: "var(--sentiment-negative)" }}>{addTickerErrorMessage(addTicker.error)}</p>
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
