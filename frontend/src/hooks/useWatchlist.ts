import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addTickerToWatchlist,
  fetchWatchlistItems,
  fetchWatchlists,
  removeTickerFromWatchlist,
} from "../api/watchlists";

export function useWatchlists() {
  return useQuery({ queryKey: ["watchlists"], queryFn: fetchWatchlists });
}

export function useWatchlistItems(watchlistId: string | undefined) {
  return useQuery({
    queryKey: ["watchlist-items", watchlistId],
    queryFn: () => fetchWatchlistItems(watchlistId as string),
    enabled: Boolean(watchlistId),
  });
}

export function useAddTicker(watchlistId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ticker: string) => {
      // Guard at the mutation itself, not just in the calling component:
      // without this, a click before useWatchlists() resolves serializes
      // `undefined` into the URL as the literal string "undefined".
      if (!watchlistId) {
        return Promise.reject(new Error("Watchlist is still loading. Try again in a moment."));
      }
      return addTickerToWatchlist(watchlistId, ticker);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchlist-items", watchlistId] });
    },
  });
}

export function useRemoveTicker(watchlistId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (companyId: string) => {
      if (!watchlistId) {
        return Promise.reject(new Error("Watchlist is still loading. Try again in a moment."));
      }
      return removeTickerFromWatchlist(watchlistId, companyId);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchlist-items", watchlistId] });
    },
  });
}
